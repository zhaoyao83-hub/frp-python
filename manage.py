import argparse
from webadmin.api.app import run_dashboard


def main():
    parser = argparse.ArgumentParser(description="MyFRP Web 管理面板")
    parser.add_argument("-c", "--config", default="config/webadmin.json", help="webadmin 配置文件路径")
    parser.add_argument("--host", default=None, help="监听地址（覆盖配置文件）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（覆盖配置文件）")
    args = parser.parse_args()
    run_dashboard(args.config, args.host, args.port)


if __name__ == "__main__":
    main()
