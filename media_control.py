#!/usr/bin/env python3

import sys
from time import sleep

import Ice

Ice.loadSlice(f'-I{Ice.getSliceDir()} spotifice_v1.ice')
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

def show_status(render, step):
    status = render.get_status()
    title = status.current_track.title if status.current_track else None
    print(f"{step:<32} -> {status.state}, {title}, repeat={status.is_repeating}")

def main(ic):
    provider = get_proxy(ic, 'Spotifice.MediaProvider.Proxy', Spotifice.MediaProviderPrx)
    render = get_proxy(ic, 'Spotifice.MediaRender.Proxy', Spotifice.MediaRenderPrx)

    print("Fetching all tracks...")
    tracks = provider.get_all_tracks()

    for t in tracks:
        print(f"- {t.title}")

    if len(tracks) < 2:
        print("At least two tracks are needed.")
        return

    track1, track2 = tracks[0], tracks[1]

    render.bind_media_provider(provider)
    render.stop()
    show_status(render, "stop()")

    render.load_track(track1.id)
    show_status(render, "load_track(track1)")

    render.play()
    show_status(render, "play()")
    sleep(3)

    render.load_track(track2.id)
    show_status(render, "load_track(track2)")
    sleep(3)

    render.pause()
    show_status(render, "pause()")
    sleep(2)

    render.previous()
    show_status(render, "previous()")

    render.play()
    show_status(render, "play()")

    render.set_repeat(True)
    show_status(render, "set_repeat(True)")

    render.stop()
    show_status(render, "stop()")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit("Usage: media_control.py --Ice.Config=<config-file>")

    with Ice.initialize(sys.argv) as communicator:
        main(communicator)
