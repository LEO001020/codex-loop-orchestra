import sys
import importlib
import traceback

sys.path.insert(0, 'E:/zcode/zloop-gen8/src')

modules = [
    'zloop.c2c', 'zloop.c2c_runner', 'zloop.checkpoint', 'zloop.cli', 'zloop.db',
    'zloop.evidence', 'zloop.history', 'zloop.hook', 'zloop.ids', 'zloop.install',
    'zloop.materialize', 'zloop.paths', 'zloop.promote', 'zloop.redact', 'zloop.stage',
    'zloop.supervisor', 'zloop.wave', 'zloop.worker_env', 'zloop.workspace',
    'zloop.__init__', 'zloop.backend.base', 'zloop.backend.codex_sdk', 'zloop.backend.__init__',
    'zloop.metrics.c2c_stats', 'zloop.metrics.concurrency', 'zloop.metrics.latency',
    'zloop.metrics.tokens', 'zloop.metrics.__init__', 'zloop.research.broker',
    'zloop.research.kimi_cli', 'zloop.research.kimi_server', 'zloop.research.port_discovery',
    'zloop.research.__init__'
]

for mod in modules:
    print(f'Importing {mod}...', end=' ', flush=True)
    try:
        importlib.import_module(mod)
        print('OK')
    except Exception as e:
        print(f'FAILED: {e}')
        traceback.print_exc()
