#!/usr/bin/env python3

import logging
import signal
import sys
import threading
import uuid
from contextlib import contextmanager
from time import sleep

import Ice
from Ice import identityToString as id2str

from gst_player import GstPlayer

Ice.loadSlice(f'-I{Ice.getSliceDir()} spotifice_v1.ice')
import Spotifice  # type: ignore # noqa: E402

State = Spotifice.PlaybackState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MediaRender")

class MediaRenderI(Spotifice.MediaRender):
    def __init__(self, player):
        self.player = player
        self.provider = None
        self.current_track = None

        self.state = State.STOPPED
        self.is_repeating = False
        self.history = []

    def ensure_provider_bound(self):
        if not self.provider:
            raise Spotifice.BadReference(reason="No MediaProvider bound")

    # --- RenderConnectivity ---

    def bind_media_provider(self, media_provider, current=None):
        try:
            proxy = media_provider.ice_timeout(500)
            proxy.ice_ping()

        except Ice.ConnectionRefusedException as e:
            raise Spotifice.BadReference(reason=f"MediaProvider not reachable: {e}")

        self.provider = media_provider
        self.history.clear()
        logger.info(f"Bound to MediaProvider '{id2str(media_provider.ice_getIdentity())}'")

    def unbind_media_provider(self, current=None):
        try:
            self.stop(current)

        except Spotifice.PlayerError as e:
            logger.error(f"Unbinding despite stop failure: {e.reason}")

        self.provider = None
        self.history.clear()
        logger.info("Unbound MediaProvider")

    # --- ContentManager ---

    def load_track(self, track_id, current=None):
        self.ensure_provider_bound()

        try:
            with self.keep_playback_state(current):
                self.set_current_track(self.provider.get_track_info(track_id))

            logger.info(f"Current track set to: {self.current_track.title}")

        except Spotifice.TrackError as e:
            logger.error(f"Error setting track: {e.reason}")
            raise

    def get_current_track(self, current=None):
        return self.current_track

    def set_current_track(self, track):
        """Cambia la pista actual y actualiza el histórico."""

        if self.current_track is not None:
            if track.id == self.current_track.id:
                return

            self.history.append(self.current_track)

        self.current_track = track

    # --- PlaybackController ---

    @contextmanager
    def keep_playback_state(self, current):
        """Mantiene el estado (Cuadro 1) mientras se cambia de pista."""

        state = self.state

        if state != State.STOPPED:
            self.stop(current)

        try:
            yield

        finally:
            if state != State.STOPPED:
                self.play(current)

            if state == State.PAUSED:
                self.pause(current)

    def play(self, current=None):
        assert current, "remote invocation required"

        self.ensure_provider_bound()

        if not self.current_track:
            raise Spotifice.TrackError(reason="No track loaded")

        if self.state == State.PLAYING:
            raise Spotifice.PlayerError(reason="Already playing")

        if self.state == State.PAUSED:
            if not self.player.resume():
                raise Spotifice.PlayerError(reason="Failed to resume")

            self.state = State.PLAYING
            return

        self.start_stream(current.id)
        self.state = State.PLAYING

    def start_stream(self, render_id):
        """Abre un flujo en el provider y arranca el player desde el principio."""

        try:
            self.provider.open_stream(render_id, self.current_track.id)

        except Spotifice.BadIdentity as e:
            logger.error(f"Error starting stream: {e.reason}")
            raise Spotifice.StreamError(reason="Stream setup failed")

        self.player.play(
            self.chunk_reader(render_id),
            lambda: self.on_track_exhausted(render_id))

        if not self.player.confirm_play_starts():
            raise Spotifice.PlayerError(reason="Failed to confirm playback")

    def on_track_exhausted(self, render_id):
        """El player avisa de que la pista ha terminado (se ejecuta en otro hilo)."""

        if self.state != State.PLAYING:
            return

        if not self.is_repeating:
            self.state = State.STOPPED
            return

        try:
            self.start_stream(render_id)

        except Exception as e:
            logger.error(f"Could not repeat the track: {e}")
            self.state = State.STOPPED

    def chunk_reader(self, render_id):
        provider = self.provider

        def read_chunk(chunk_size):
            try:
                return provider.get_chunk(render_id, chunk_size)

            except Spotifice.IOError as e:
                logger.error(f"Playback of track '{e.item}' aborted: {e.reason}")

            except Ice.Exception as e:
                logger.critical(e)

        return read_chunk

    def stop(self, current=None):
        if self.provider and current:
            self.provider.close_stream(current.id)

        if not self.player.stop():
            raise Spotifice.PlayerError(reason="Failed to confirm stop")

        self.state = State.STOPPED
        logger.info("Stopped")

    def pause(self, current=None):
        if self.state == State.STOPPED:
            raise Spotifice.PlayerError(reason="Cannot pause when stopped")

        if self.state == State.PAUSED:
            return

        if not self.player.pause():
            raise Spotifice.PlayerError(reason="Failed to confirm pause")

        self.state = State.PAUSED
        logger.info("Paused")

    def get_status(self, current=None):
        return Spotifice.PlaybackStatus(
            state=self.state,
            current_track=self.current_track,
            is_repeating=self.is_repeating)

    def previous(self, current=None):
        if not self.history:
            return

        actual = self.current_track

        try:
            with self.keep_playback_state(current):
                self.current_track = self.history[-1]

        except Exception:
            self.current_track = actual
            raise

        self.history.pop()
        logger.info(f"Current track set to: {self.current_track.title}")

    def set_repeat(self, enabled, current=None):
        self.is_repeating = enabled
        logger.info(f"Repeat {'enabled' if enabled else 'disabled'}")

def shutdown_on_interrupt(ic):
    def shutdown(*_):
        ic.shutdown()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT,  shutdown)
        signal.signal(signal.SIGTERM, shutdown)

def main(ic, player):
    shutdown_on_interrupt(ic)

    servant = MediaRenderI(player)

    properties = ic.getProperties()
    identity = properties.getPropertyWithDefault(
        'Spotifice.MediaRender.Identity',  str(uuid.uuid1()))

    adapter = ic.createObjectAdapter('Spotifice.MediaRenderAdapter')
    proxy = adapter.add(servant, ic.stringToIdentity(identity))
    logger.info(f"MediaRender: {proxy}")

    adapter.activate()
    while not ic.isShutdown():
        sleep(0.5)

    logger.info("Server shutdown.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: media_render.py --Ice.Config=<config-file>")

    with Ice.initialize(sys.argv) as communicator, GstPlayer() as player:
        main(communicator, player)
