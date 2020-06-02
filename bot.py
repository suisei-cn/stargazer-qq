import logging
from os import path

import nonebot

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    nonebot.init()
    # nonebot.load_builtin_plugins()
    nonebot.load_plugins(
        path.join(path.dirname(__file__), 'observatory', 'plugins'),
        'observatory.plugins'
    )
    nonebot.run(host="0.0.0.0", port=8080)
