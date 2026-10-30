import asyncio
import errno
import sys
import uvicorn


def install_windows_asyncio_shield() -> None:
    """
    Corrige o bug crítico do Python 3.12 no Windows (WinError 64 em IocpProactor.accept /
    ProactorEventLoop._start_serving) onde um reset de conexão TCP local (ex: extensões ou
    monitores locais sondando a porta 8000) derruba permanentemente o socket de escuta do Uvicorn
    e faz o navegador receber `NetworkError when attempting to fetch resource`.
    """
    if sys.platform != "win32":
        return

    # 1. Define a política padrão para SelectorEventLoop (que usa select() em vez de IOCP AcceptEx)
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

    # 2. Blindagem adicional no ProactorEventLoop/IocpProactor caso uvicorn seja iniciado via CLI
    try:
        import asyncio.proactor_events as proactor_events
        import asyncio.windows_events as windows_events

        orig_accept = windows_events.IocpProactor.accept

        def safe_accept(self, listener):
            fut = orig_accept(self, listener)

            def _silence_transient_accept_exc(f):
                try:
                    f.exception()
                except Exception:
                    pass

            fut.add_done_callback(_silence_transient_accept_exc)
            return fut

        windows_events.IocpProactor.accept = safe_accept

        orig_start_serving = proactor_events.ProactorEventLoop._start_serving

        def safe_start_serving(
            self,
            protocol_factory,
            sock,
            sslcontext=None,
            server=None,
            backlog=100,
            ssl_handshake_timeout=None,
            ssl_shutdown_timeout=None,
        ):
            def loop(f=None):
                try:
                    if f is not None:
                        conn, addr = f.result()
                        protocol = protocol_factory()
                        if sslcontext is not None:
                            self._make_ssl_transport(
                                conn,
                                protocol,
                                sslcontext,
                                server_side=True,
                                extra={"peername": addr},
                                server=server,
                                ssl_handshake_timeout=ssl_handshake_timeout,
                                ssl_shutdown_timeout=ssl_shutdown_timeout,
                            )
                        else:
                            self._make_socket_transport(
                                conn, protocol, extra={"peername": addr}, server=server
                            )
                    if server is not None and not server.is_serving():
                        return
                    f = self._proactor.accept(sock)
                except OSError as exc:
                    if sock.fileno() != -1:
                        # WinError 64 (ERROR_NETNAME_DELETED), 121, 995, 10053, 10054 ou EINVAL:
                        # Nunca encerrar o loop de aceite do servidor HTTP!
                        self.call_later(0.05, loop)
                except asyncio.CancelledError:
                    sock.close()
                else:
                    f.add_done_callback(loop)

            self.call_soon(loop)

        proactor_events.ProactorEventLoop._start_serving = safe_start_serving
    except Exception:
        pass


def _is_port_in_use(host: str = "127.0.0.1", port: int = 8000) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _open_browser_url(url: str) -> None:
    import shutil
    import subprocess
    import webbrowser

    try:
        if sys.platform.startswith("linux") and shutil.which("xdg-open"):
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
    except Exception:
        pass
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _schedule_browser_open(url: str, port: int = 8000) -> None:
    import threading
    import time

    def _worker() -> None:
        for _ in range(30):
            if _is_port_in_use("127.0.0.1", port):
                _open_browser_url(url)
                return
            time.sleep(0.25)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


if __name__ == "__main__":
    import os

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    port = int(os.environ.get("ANTIGRAVITY_PORT", "8000"))
    url = f"http://127.0.0.1:{port}"
    no_browser = (
        "--no-browser" in sys.argv
        or os.environ.get("ANTIGRAVITY_NO_BROWSER", "").strip() in ("1", "true", "yes")
    )
    should_open_browser = (
        not no_browser
        and (getattr(sys, "frozen", False) or "--open-browser" in sys.argv)
    )

    if _is_port_in_use("127.0.0.1", port):
        print(f">> Antigravity Studio ja esta em execucao em {url} ...")
        if not no_browser:
            _open_browser_url(url)
        sys.exit(0)

    install_windows_asyncio_shield()
    from app.main import app as fastapi_app

    print(f">> Iniciando Antigravity Studio Playground em {url} ...")
    if should_open_browser:
        _schedule_browser_open(url, port=port)

    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        config = uvicorn.Config(
            fastapi_app,
            host="0.0.0.0",
            port=port,
            reload=False,
            loop="none",
        )
        server = uvicorn.Server(config)
        loop.run_until_complete(server.serve())
    else:
        uvicorn.run(fastapi_app, host="0.0.0.0", port=port, reload=False)

