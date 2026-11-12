#!/usr/bin/env python3

import sys
from time import sleep

import Ice

Ice.loadSlice(f'-I{Ice.getSliceDir()} spotifice_v2.ice')
import Spotifice  # type: ignore # noqa: E402


def get_proxy(ic, property, cls):
    proxy = ic.propertyToProxy(property)

    for _ in range(5):
        try:
            proxy.ice_ping()
            break
        except Ice.ConnectionRefusedException:
            sleep(0.5)

    object = cls.checkedCast(proxy)
    if object is None:
        raise RuntimeError(f'Invalid proxy for {property}')

    return object


def show_track(render, step):
    track = render.get_current_track()
    print(f"{step:<28} -> {track.title if track else None}")


def main(ic):
    provider = get_proxy(ic, 'Spotifice.MediaProvider.Proxy', Spotifice.MediaProviderPrx)
    render = get_proxy(ic, 'Spotifice.MediaRender.Proxy', Spotifice.MediaRenderPrx)

    print("Fetching all playlists...")
    playlists = provider.get_all_playlists()
    for p in playlists:
        print(f"- {p.name} ({p.id})")

    if not playlists:
        print("No playlists found.")
        return

    render.bind_media_provider(provider)

    render.load_playlist(playlists[0].id)
    show_track(render, "load_playlist()")

    render.play()
    show_track(render, "play()")
    sleep(3)

    render.next()
    show_track(render, "next()")
    sleep(3)

    render.stop()
    show_track(render, "stop()")

    render.previous()
    show_track(render, "previous()")

    render.play()
    show_track(render, "play()")
    sleep(3)

    render.set_repeat(True)
    render.next()
    show_track(render, "set_repeat(True) + next()")
    sleep(3)

    render.stop()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit("Usage: media_control.py --Ice.Config=<config-file>")

    with Ice.initialize(sys.argv) as communicator:
        main(communicator)
