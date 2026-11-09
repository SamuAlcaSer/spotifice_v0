from pathlib import Path

from media_provider import main as provider_main
from media_render import Spotifice
from media_render import main as render_main
from metadata import identify

from gst_player import GstPlayer

from .icetest import IceTestCase

from time import sleep

MEDIA = Path('test/media')

class TestRender(IceTestCase):
    provider_port = 10000
    provider_identity = 'mediaProvider0'
    render_port = 10001
    render_identity = 'mediaRender0'

    def setUp(self):
        provider_props = {
            'Spotifice.MediaProviderAdapter.Endpoints': f'tcp -p {self.provider_port}',
            'Spotifice.MediaProvider.Content': str(MEDIA),
            'Spotifice.MediaProvider.Identity': self.provider_identity
        }
        provider_strprx = f'{self.provider_identity}:tcp -p {self.provider_port} -t 500'
        self.create_server(provider_main, provider_props)

        player = GstPlayer()
        player.start()
        self.addCleanup(player.shutdown)

        render_props = {
            'Spotifice.MediaRenderAdapter.Endpoints': f'tcp -p {self.render_port}',
            'Spotifice.MediaRender.Identity': self.render_identity
        }
        render_strprx = f'{self.render_identity}:tcp -p {self.render_port} -t 500'
        self.create_server(render_main, render_props, player)

        self.provider = self.create_proxy(provider_strprx, Spotifice.MediaProviderPrx)
        self.music_lib = self.stream_mngr = self.provider
        self.sut = self.create_proxy(render_strprx, Spotifice.MediaRenderPrx)

    def bind(self):
        self.sut.bind_media_provider(self.provider)

class PlaybackTestsBase(TestRender):
    def test_id(self):
        self.assertEqual(self.sut.ice_id(), '::Spotifice::MediaRender')

    def test_stop_is_idempotent(self):
        self.sut.stop()
        self.sut.stop()

    def test_play_unbound_provider(self):
        with self.assertRaises(Spotifice.BadReference) as cm:
            self.sut.play()

        self.assertEqual(cm.exception.reason, "No MediaProvider bound")

    def test_play_unloaded_track(self):
        self.bind()

        with self.assertRaises(Spotifice.TrackError) as cm:
            self.sut.play()

        self.assertEqual(cm.exception.reason, "No track loaded")

    def test_normal_play(self):
        self.bind()
        self.sut.load_track(identify(MEDIA / '2s.mp3'))

        self.sut.play()

    def test_can_not_play_if_player_busy(self):
        self.bind()
        self.sut.load_track(identify(MEDIA / '2s.mp3'))

        self.sut.play()

        with self.assertRaises(Spotifice.PlayerError) as cm:
            self.sut.play()

        self.assertEqual(cm.exception.reason, "Already playing")

    def test_load_track_while_playing(self):
        self.bind()
        self.sut.load_track(identify(MEDIA / '4s.mp3'))
        self.sut.play()

        self.sut.load_track(identify(MEDIA / '2s.mp3'))

        self.assertEqual(self.sut.get_current_track().id, identify(MEDIA / '2s.mp3'))

State = Spotifice.PlaybackState

class StateTestsBase(TestRender):
    def setUp(self):
        super().setUp()
        self.bind()
        self.a = identify(MEDIA / '4s.mp3')
        self.b = identify(MEDIA / '2s.mp3')
        self.c = identify(MEDIA / '1s.mp3')
        self.sut.load_track(self.a)

    def assertStatus(self, state, track_id):
        status = self.sut.get_status()
        self.assertEqual(status.state, state)
        self.assertEqual(status.current_track.id, track_id)

    # --- Cuadro 1 ---

    def test_play_from_paused(self):
        self.sut.play()
        self.sut.pause()
        self.sut.play()
        self.assertStatus(State.PLAYING, self.a)

    def test_pause_when_playing(self):
        self.sut.play()
        self.sut.pause()
        self.assertStatus(State.PAUSED, self.a)

    def test_pause_is_idempotent(self):
        self.sut.play()
        self.sut.pause()
        self.sut.pause()
        self.assertStatus(State.PAUSED, self.a)

    def test_pause_when_stopped(self):
        with self.assertRaises(Spotifice.PlayerError) as cm:
            self.sut.pause()

        self.assertEqual(cm.exception.reason, "Cannot pause when stopped")

    def test_stop_when_paused(self):
        self.sut.play()
        self.sut.pause()
        self.sut.stop()
        self.assertStatus(State.STOPPED, self.a)

    def test_load_track_when_stopped(self):
        self.sut.load_track(self.b)
        self.assertStatus(State.STOPPED, self.b)

    def test_load_track_when_playing(self):
        self.sut.play()
        self.sut.load_track(self.b)
        self.assertStatus(State.PLAYING, self.b)

    def test_load_track_when_paused(self):
        self.sut.play()
        self.sut.pause()
        self.sut.load_track(self.b)
        self.assertStatus(State.PAUSED, self.b)

    def test_previous_keeps_state(self):
        self.sut.load_track(self.b)
        self.sut.play()
        self.sut.previous()
        self.assertStatus(State.PLAYING, self.a)

    # --- Cuadro 3 (los ejemplos con solo dos pistas) ---

    def test_previous(self):
        self.sut.load_track(self.b)
        self.sut.previous()
        self.assertStatus(State.STOPPED, self.a)

    def test_previous_twice(self):
        self.sut.load_track(self.b)
        self.sut.previous()
        self.sut.previous()
        self.assertStatus(State.STOPPED, self.a)

    def test_reloading_current_track_keeps_history(self):
        self.sut.load_track(self.b)
        self.sut.load_track(self.b)
        self.sut.previous()
        self.assertStatus(State.STOPPED, self.a)

    def test_bind_clears_history(self):
        self.sut.load_track(self.b)
        self.bind()
        self.sut.previous()
        self.assertStatus(State.STOPPED, self.b)

    # --- Repetición y final de pista ---

    def test_repeat_is_disabled_by_default(self):
        self.assertFalse(self.sut.get_status().is_repeating)

    def test_track_end_without_repeat(self):
        self.sut.load_track(self.b)
        self.sut.play()
        sleep(4)
        self.assertStatus(State.STOPPED, self.b)

    def test_track_end_with_repeat(self):
        self.sut.load_track(self.b)
        self.sut.set_repeat(True)
        self.sut.play()
        sleep(4)
        self.assertStatus(State.PLAYING, self.b)

    # --- Cuadro 3 (ejemplos con tres pistas) ---

    def test_previous_after_three_loads(self):
        self.sut.load_track(self.b)
        self.sut.load_track(self.c)
        self.sut.previous()
        self.sut.previous()
        self.assertStatus(State.STOPPED, self.a)

    def test_load_after_previous(self):
        self.sut.load_track(self.b)
        self.sut.previous()
        self.sut.load_track(self.c)
        self.sut.previous()
        self.assertStatus(State.STOPPED, self.a)

    # --- Casos límite ---

    def test_previous_without_history_does_nothing(self):
        self.sut.previous()
        self.assertStatus(State.STOPPED, self.a)

    def test_unbind_stops_playback_and_clears_history(self):
        self.sut.load_track(self.b)
        self.sut.play()
        self.sut.unbind_media_provider()
        self.assertEqual(self.sut.get_status().state, State.STOPPED)

        self.bind()
        self.sut.previous()
        self.assertStatus(State.STOPPED, self.b)
