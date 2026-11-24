"""Run the map: python -m aredn_services_map --config config.yaml"""
import argparse
import logging


def main() -> None:
    ap = argparse.ArgumentParser(prog="aredn-services-map", description=__doc__)
    ap.add_argument("--config", "-c", help="YAML config file (see config.example.yaml)")
    ap.add_argument("--listen", help="address to listen on (default from config, else 0.0.0.0)")
    ap.add_argument("--port", type=int, help="port (default from config, else 8088)")
    ap.add_argument("--log-level", default="info")
    ap.add_argument("--update", action="store_true",
                    help="install the latest release from GitHub if it is newer, then exit (restart the service after)")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s  %(message)s")
    if args.update:
        import asyncio
        from aredn_services_map import updater
        u = asyncio.run(updater.check(force=True))
        if u["error"]:
            raise SystemExit(f"Could not check GitHub: {u['error']}")
        if not u["available"]:
            print(f"Up to date: {u['current']} (latest release {u['latest']})")
            return
        print(f"Installing {u['latest']} (this is {u['current']}) ...")
        for line in updater.install(u["latest"]):
            print("  " + line)
        print("Done. Restart the map to run it (systemctl restart aredn-services-map).")
        return

    import uvicorn
    from aredn_services_map.server import create_app, load_config
    cfg = load_config(args.config)
    uvicorn.run(create_app(cfg), host=args.listen or cfg["listen"], port=args.port or int(cfg["port"]),
                log_level=args.log_level.lower(), proxy_headers=False)


if __name__ == "__main__":
    main()
