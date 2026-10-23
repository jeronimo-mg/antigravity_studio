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


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    install_windows_asyncio_shield()
    from app.main import app as fastapi_app

    print(">> Iniciando Antigravity Studio Playground em http://127.0.0.1:8000 ...")
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        config = uvicorn.Config(
            fastapi_app,
            host="0.0.0.0",
            port=8000,
            reload=False,
            loop="none",
        )
        server = uvicorn.Server(config)
        loop.run_until_complete(server.serve())
    else:
        uvicorn.run(fastapi_app, host="0.0.0.0", port=8000, reload=False)
