import logging
import os

import nonebot

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    if dsn := os.environ.get("TELEMETRY", ""):
        import sentry_sdk

        sentry_sdk.init(dsn, release=os.environ.get("TELEMETRY_RELEASE", None))

    nonebot.init()
    # nonebot.load_builtin_plugins()
    nonebot.load_plugins(
        os.path.join(os.path.dirname(__file__), 'observatory', 'plugins'),
        'observatory.plugins'
    )
    nonebot.run(host="0.0.0.0", port=8080)
