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

Ice.loadSlice(f'-I{Ice.getSliceDir()} spotifice_v2.ice')
import Spotifice  # type: ignore # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MediaRender")


class MediaRenderI(Spotifice.MediaRender):
    def __init__(self, player):
        self.player = player
        self.provider = None
        self.current_track = None

        self.playlist = None  # Playlist cargada (None si no hay ninguna)
        self.playlist_pos = 0  # posición de la pista actual dentro de la playlist
        self.is_repeating = False

    def ensure_player_stopped(self):
        if self.player.is_playing():
            raise Spotifice.PlayerError(reason="Already playing")

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
        self.playlist = None
        logger.info(f"Bound to MediaProvider '{id2str(media_provider.ice_getIdentity())}'")

    def unbind_media_provider(self, current=None):
        try:
            self.stop(current)
        except Spotifice.PlayerError as e:
            logger.error(f"Unbinding despite stop failure: {e.reason}")

        self.provider = None
        self.playlist = None
        logger.info("Unbound MediaProvider")

    # --- ContentManager ---

    def load_track(self, track_id, current=None):
        self.ensure_provider_bound()

        try:
            self.change_track(track_id, current)
            self.playlist = None  # cargar una pista suelta descarta la playlist

            logger.info(f"Current track set to: {self.current_track.title}")

        except Spotifice.TrackError as e:
            logger.error(f"Error setting track: {e.reason}")
            raise

    def get_current_track(self, current=None):
        return self.current_track

    def load_playlist(self, playlist_id, current=None):
        self.ensure_provider_bound()

        playlist = self.provider.get_playlist(playlist_id)  # PlaylistError si no existe
        if not playlist.track_ids:
            raise Spotifice.PlaylistError(playlist_id, "Playlist is empty")

        # Primero se cambia la pista; si falla, la playlist anterior no se toca
        self.change_track(playlist.track_ids[0], current)
        self.playlist = playlist
        self.playlist_pos = 0

        logger.info(f"Playlist loaded: {playlist.name}")

    def change_track(self, track_id, current):
        """Cambia la pista actual conservando el estado del reproductor."""
        with self.keep_playing_state(current):
            self.current_track = self.provider.get_track_info(track_id)

    def go_to(self, position, current):
        """Salta a la pista de la playlist que está en esa posición."""
        self.change_track(self.playlist.track_ids[position], current)
        self.playlist_pos = position

    def next_position(self):
        """Posición de la pista siguiente en la playlist, o None si no hay."""
        if self.playlist is None:
            return None

        position = self.playlist_pos + 1
        if position < len(self.playlist.track_ids):
            return position

        return 0 if self.is_repeating else None

    # --- PlaybackController ---

    @contextmanager
    def keep_playing_state(self, current):
        playing = self.player.is_playing()
        if playing:
            self.stop(current)
        try:
            yield
        finally:
            if playing:
                self.play(current)

    def play(self, current=None):
        assert current, "remote invocation required"

        self.ensure_provider_bound()

        if not self.current_track:
            raise Spotifice.TrackError(reason="No track loaded")

        self.ensure_player_stopped()

        try:
            self.provider.open_stream(current.id, self.current_track.id)
        except Spotifice.BadIdentity as e:
            logger.error(f"Error starting stream: {e.reason}")
            raise Spotifice.StreamError(reason="Strean setup failed")

        self.player.play(
            self.chunk_reader(current.id),
            lambda: self.on_track_exhausted(current))
        if not self.player.confirm_play_starts():
            raise Spotifice.PlayerError(reason="Failed to confirm playback")

    def on_track_exhausted(self, current):
        """El player avisa de que la pista ha terminado (se ejecuta en otro hilo).

        El player ya ha detenido su flujo, así que si no hay pista siguiente
        (sin playlist, o última sin repetición) no hay nada más que hacer.
        """
        position = self.next_position()
        if position is None:
            return

        try:
            self.go_to(position, current)
            self.play(current)
        except Exception as e:
            logger.error(f"Could not play the next track: {e}")

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

        logger.info("Stopped")

    def next(self, current=None):
        position = self.next_position()
        if position is not None:
            self.go_to(position, current)

    def previous(self, current=None):
        if self.playlist is None or self.playlist_pos == 0:
            return  # sin playlist, o ya en la primera pista (aunque haya repetición)

        self.go_to(self.playlist_pos - 1, current)

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
