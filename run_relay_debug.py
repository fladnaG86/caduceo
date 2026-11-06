import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

import asyncio
from caduceo_relay.server import main

asyncio.run(main())