"""Optional isolated seven-tab UI fixture. Never opens real application data."""
import argparse
import tempfile
from pathlib import Path

from cleaner.demo import create_demo
from cleaner.providers import make_providers
from cleaner.server import AppServer
from tests.test_opencode import create_opencode


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url-file',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='cleaner-browser-demo-') as directory:
        profile=Path(directory)
        home=profile/'.codex';home.mkdir();create_demo(home)
        create_opencode(profile)
        providers=make_providers(profile=profile)
        for provider in providers.values():
            provider.process_provider=lambda: []
        providers['codex'].rpc_factory=None
        with AppServer(providers['codex'],demo=True,providers=providers) as server:
            args.url_file.parent.mkdir(parents=True,exist_ok=True)
            args.url_file.write_text(server.origin+'/#token='+server.token,encoding='utf-8')
            print('Isolated browser demo ready.',flush=True)
            server.serve_forever(poll_interval=.3)


if __name__=='__main__':main()
