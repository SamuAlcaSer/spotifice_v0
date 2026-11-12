import json
import tempfile
from pathlib import Path
from time import sleep

from media_provider import main as provider_main
from media_render import Spotifice
from media_render import main as render_main
from metadata import identify

from gst_player import GstPlayer

from .icetest import IceTestCase

MEDIA = Path('test/media')


class TestRender(IceTestCase):
    provider_port = 10000
    provider_identity = 'mediaProvider0'
    render_port = 10001
    render_identity = 'mediaRender0'

    def extra_provider_props(self):
        return {}

    def setUp(self):
        provider_props = {
            'Spotifice.MediaProviderAdapter.Endpoints': f'tcp -p {self.provider_port}',
            'Spotifice.MediaProvider.Content': str(MEDIA),
            'Spotifice.MediaProvider.Identity': self.provider_identity
        }
        provider_props.update(self.extra_provider_props())
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


# --- Playlists (ejercicio 2) ---

class PlaylistTestsBase(TestRender):
    def extra_provider_props(self):
        """Prepara playlists de prueba en un directorio temporal."""
        self.t1 = identify(MEDIA / '1s.mp3')
        self.t2 = identify(MEDIA / '2s.mp3')
        self.t3 = identify(MEDIA / '4s.mp3')

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)

        playlists = {
            'abc': [self.t1, self.t2, self.t3],
            'empty': [],
            'pair': [self.t1, self.t3],
            'single': [self.t1],
        }
        for playlist_id, track_ids in playlists.items():
            data = {
                'id': playlist_id,
                'name': playlist_id,
                'description': '',
                'owner': 'Tester',
                'created_at': '01-01-2026',
                'track_ids': track_ids,
            }
            (folder / f'{playlist_id}.playlist').write_text(json.dumps(data))

        return {'Spotifice.MediaProvider.Playlists': str(folder)}

    def setUp(self):
        super().setUp()
        self.bind()

    def current_id(self):
        track = self.sut.get_current_track()
        return track.id if track else None

    def is_playing(self):
        """Si estaba parado lo deja parado (play() solo falla si ya está sonando)."""
        try:
            self.sut.play()
        except Spotifice.PlayerError:
            return True

        self.sut.stop()
        return False

    # --- load_playlist ---

    def test_load_playlist_loads_the_first_track(self):
        self.sut.load_playlist('abc')

        self.assertEqual(self.current_id(), self.t1)
        self.assertFalse(self.is_playing())

    def test_load_unknown_playlist(self):
        with self.assertRaises(Spotifice.PlaylistError) as cm:
            self.sut.load_playlist('bad-playlist-id')

        self.assertEqual(cm.exception.reason, 'Playlist not found')
        self.assertIsNone(self.current_id())

    def test_load_empty_playlist_does_not_change_the_player(self):
        self.sut.load_track(self.t3)

        with self.assertRaises(Spotifice.PlaylistError):
            self.sut.load_playlist('empty')

        self.assertEqual(self.current_id(), self.t3)

    # --- next / previous ---

    def test_next_and_previous_without_playlist_do_nothing(self):
        self.sut.load_track(self.t2)

        self.sut.next()
        self.assertEqual(self.current_id(), self.t2)

        self.sut.previous()
        self.assertEqual(self.current_id(), self.t2)

    def test_next(self):
        self.sut.load_playlist('abc')

        self.sut.next()
        self.assertEqual(self.current_id(), self.t2)

        self.sut.next()
        self.assertEqual(self.current_id(), self.t3)

    def test_next_at_the_last_track_does_nothing(self):
        self.sut.load_playlist('abc')
        self.sut.next()
        self.sut.next()

        self.sut.next()

        self.assertEqual(self.current_id(), self.t3)

    def test_next_at_the_last_track_with_repeat_goes_to_the_first(self):
        self.sut.load_playlist('abc')
        self.sut.set_repeat(True)
        self.sut.next()
        self.sut.next()

        self.sut.next()

        self.assertEqual(self.current_id(), self.t1)

    def test_previous(self):
        self.sut.load_playlist('abc')
        self.sut.next()
        self.sut.next()

        self.sut.previous()

        self.assertEqual(self.current_id(), self.t2)

    def test_previous_at_the_first_track_does_nothing_even_with_repeat(self):
        self.sut.load_playlist('abc')
        self.sut.set_repeat(True)

        self.sut.previous()

        self.assertEqual(self.current_id(), self.t1)

    def test_next_while_playing_keeps_playing(self):
        self.sut.load_playlist('abc')
        self.sut.play()

        self.sut.next()

        self.assertEqual(self.current_id(), self.t2)
        self.assertTrue(self.is_playing())

    def test_next_while_stopped_keeps_stopped(self):
        self.sut.load_playlist('abc')

        self.sut.next()

        self.assertEqual(self.current_id(), self.t2)
        self.assertFalse(self.is_playing())

    # --- La playlist se descarta ---

    def test_load_track_discards_the_playlist(self):
        self.sut.load_playlist('abc')
        self.sut.load_track(self.t3)

        self.sut.next()

        self.assertEqual(self.current_id(), self.t3)

    def test_bind_discards_the_playlist(self):
        self.sut.load_playlist('abc')
        self.bind()

        self.sut.next()

        self.assertEqual(self.current_id(), self.t1)

    def test_unbind_discards_the_playlist(self):
        self.sut.load_playlist('abc')
        self.sut.unbind_media_provider()
        self.bind()

        self.sut.next()

        self.assertEqual(self.current_id(), self.t1)

    # --- Fin de pista ---

    def test_track_end_loads_the_next_one_and_keeps_playing(self):
        self.sut.load_playlist('pair')  # 1s + 4s
        self.sut.play()

        sleep(2.5)

        self.assertEqual(self.current_id(), self.t3)
        self.assertTrue(self.is_playing())

    def test_last_track_end_without_repeat_stops(self):
        self.sut.load_playlist('single')  # 1s
        self.sut.play()

        sleep(3)

        self.assertEqual(self.current_id(), self.t1)
        self.assertFalse(self.is_playing())

    def test_last_track_end_with_repeat_starts_again(self):
        self.sut.load_playlist('single')  # 1s
        self.sut.set_repeat(True)
        self.sut.play()

        sleep(3)

        self.assertEqual(self.current_id(), self.t1)
        self.assertTrue(self.is_playing())
