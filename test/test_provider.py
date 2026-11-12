import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import Ice
from media_provider import Spotifice, main
from metadata import identify

from .icetest import IceTestCase

MEDIA = Path('test/media')


class TestProvider(IceTestCase):
    provider_port = 10000
    provider_identity = 'mediaProvider0'

    def setUp(self):
        provider_props = {
            'Spotifice.MediaProviderAdapter.Endpoints': f'tcp -p {self.provider_port}',
            'Spotifice.MediaProvider.Content': str(MEDIA),
            'Spotifice.MediaProvider.Identity': self.provider_identity
        }
        provider_strprx = f'{self.provider_identity}:tcp -p {self.provider_port} -t 500'
        self.create_server(main, provider_props)
        self.sut = self.create_proxy(provider_strprx, Spotifice.MediaProviderPrx)


class MusicLibraryTestsBase(TestProvider):
    def test_get_all_tracks(self):
        tracks = self.sut.get_all_tracks()
        self.assertEqual(len(tracks), 4)
        self.assertEqual(tracks[0].title, '1s')

    def test_get_track_info(self):
        track = self.sut.get_track_info(identify(MEDIA / '1s.mp3'))
        self.assertEqual(track.title, '1s')

    def test_track_id_does_not_depend_on_the_filename(self):
        track = self.sut.get_track_info(identify(MEDIA / '1s.mp3'))

        self.assertNotIn('1s', track.id)
        self.assertNotIn('.mp3', track.id)

    def test_get_track_info_wrong_track(self):
        with self.assertRaises(Spotifice.TrackError) as cm:
            self.sut.get_track_info('bad-track-id')

        self.assertEqual(cm.exception.item, 'bad-track-id')
        self.assertEqual(cm.exception.reason, 'Track not found')


class StreamManagerTestsBase(TestProvider):
    def test_open_stream_wrong_track(self):
        track_id = 'bad-track-id'
        render_id = self.client_ic.stringToIdentity('bad-render-id')

        with self.assertRaises(Spotifice.TrackError) as cm:
            self.sut.open_stream(render_id, track_id)

        self.assertEqual(cm.exception.item, 'bad-track-id')
        self.assertEqual(cm.exception.reason, 'Track not found')

    def test_get_audio_chunk(self):
        track_id = self.sut.get_all_tracks()[0].id
        render_id = Ice.Identity(name='fake-render-id')


        self.sut.open_stream(render_id, track_id)
        chunk = self.sut.get_chunk(render_id, 1024)

        self.assertEqual(len(chunk), 1024)
        self.assertTrue(chunk.startswith(b'OggS'))


class StreamManagerNoAuthBase(TestProvider):
    def test_get_audio_chunk_not_started_stream(self):
        render_id = Ice.Identity(name='missing-render-id')

        with self.assertRaises(Spotifice.StreamError) as cm:
            self.sut.get_chunk(render_id, 1024)

        self.assertEqual(cm.exception.item, 'missing-render-id')
        self.assertEqual(cm.exception.reason, 'No open stream for render')

    def test_open_stream_wrong_render(self):
        tracks = self.sut.get_all_tracks()
        track_id = tracks[0].id
        render_id = Ice.Identity(name='')

        with self.assertRaises(Spotifice.BadIdentity) as cm:
            self.sut.open_stream(render_id, track_id)

        self.assertEqual(cm.exception.item, '')
        self.assertEqual(cm.exception.reason, 'Invalid render identity')


# --- Playlists (ejercicio 2) ---

class TestProviderPlaylists(IceTestCase):
    provider_port = 10000
    provider_identity = 'mediaProvider0'

    def setUp(self):
        provider_props = {
            'Spotifice.MediaProviderAdapter.Endpoints': f'tcp -p {self.provider_port}',
            'Spotifice.MediaProvider.Content': str(MEDIA),
            'Spotifice.MediaProvider.Playlists': str(self.prepare_playlists()),
            'Spotifice.MediaProvider.Identity': self.provider_identity
        }
        provider_strprx = f'{self.provider_identity}:tcp -p {self.provider_port} -t 500'
        self.create_server(main, provider_props)
        self.sut = self.create_proxy(provider_strprx, Spotifice.MediaProviderPrx)

    def prepare_playlists(self):
        """Crea un directorio temporal con playlists de prueba."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)

        self.track_ids = [identify(MEDIA / name) for name in ('1s.mp3', '2s.mp3')]
        good = {
            'id': 'test-list',
            'name': 'Test list',
            'description': 'A list for testing',
            'owner': 'Tester',
            'created_at': '25-05-2011',
            'track_ids': [self.track_ids[0], 'unknown-track-id', self.track_ids[1]],
        }

        (folder / 'good.playlist').write_text(json.dumps(good))
        (folder / 'broken.playlist').write_text('{ this is not json')
        (folder / 'incomplete.playlist').write_text(json.dumps({'id': 'incomplete'}))
        (folder / 'ignored.txt').write_text(json.dumps({**good, 'id': 'ignored'}))

        return folder


class PlaylistManagerTestsBase(TestProviderPlaylists):
    def test_only_valid_playlists_are_loaded(self):
        playlists = self.sut.get_all_playlists()

        self.assertEqual([each.id for each in playlists], ['test-list'])

    def test_get_playlist(self):
        playlist = self.sut.get_playlist('test-list')

        self.assertEqual(playlist.name, 'Test list')
        self.assertEqual(playlist.description, 'A list for testing')
        self.assertEqual(playlist.owner, 'Tester')

    def test_unknown_track_ids_are_discarded(self):
        playlist = self.sut.get_playlist('test-list')

        self.assertEqual(playlist.track_ids, self.track_ids)

    def test_created_at_is_seconds_since_epoch(self):
        playlist = self.sut.get_playlist('test-list')

        expected = int(datetime(2011, 5, 25, tzinfo=timezone.utc).timestamp())
        self.assertEqual(playlist.created_at, expected)

    def test_get_playlist_wrong_id(self):
        with self.assertRaises(Spotifice.PlaylistError) as cm:
            self.sut.get_playlist('bad-playlist-id')

        self.assertEqual(cm.exception.item, 'bad-playlist-id')
        self.assertEqual(cm.exception.reason, 'Playlist not found')


class NoPlaylistsDirectoryTestsBase(TestProviderPlaylists):
    def prepare_playlists(self):
        return Path('test/this-directory-does-not-exist')

    def test_no_directory_means_no_playlists(self):
        self.assertEqual(self.sut.get_all_playlists(), [])
