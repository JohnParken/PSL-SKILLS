"""Public CLI capabilities only: no XLSX/ET Python imports or Workbook internals."""
import atexit
from decimal import Context, DecimalException, DivisionByZero, Inexact, InvalidOperation, Overflow, ROUND_HALF_EVEN, Underflow, localcontext
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace
import weakref

from table_workflow_cli_protocol import PROTOCOL, MAX_FRAME, dumps, loads

CLIENTS = weakref.WeakSet()


def close_clients():
    for client in list(CLIENTS):
        client.close()


atexit.register(close_clients)


class CapabilityError(ValueError):
    def __init__(self, error):
        super().__init__(error.get('message', 'Capability failed'))
        self.diagnostics = error.get('diagnostics', {})
        if error.get('calculation') is not None:
            self.calculation = error['calculation']
        self.remote_code = error.get('code')
        self.status = error.get('status')
        self.location = error.get('location')


class Client:
    def __init__(self, root):
        self.script = str(root / 'scripts' / 'xlsx_cli.py')
        self.process = None
        self.cwd = None
        self.identity = 0
        self.metrics = {'requests': 0, 'request_bytes': 0, 'response_bytes': 0}
        self.lock = threading.Lock()
        self.stderr = bytearray()
        self.drainer = None
        self.stderr_truncated = False
        CLIENTS.add(self)

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if self.drainer is not None:
                self.drainer.join(timeout=1)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def start(self):
        cwd = Path.cwd()
        if self.process is not None and self.cwd != cwd:
            self.close()
        if self.process is None:
            self.cwd = cwd
            self.stderr.clear()
            self.stderr_truncated = False
            self.process = subprocess.Popen([sys.executable, '-I', '-S', '-B', self.script, 'serve'],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            process = self.process
            def drain():
                while True:
                    chunk = process.stderr.read(4096)
                    if not chunk:
                        break
                    remaining = max(0, 8192 - len(self.stderr))
                    self.stderr.extend(chunk[:remaining])
                    self.stderr_truncated |= len(chunk) > remaining
            self.drainer = threading.Thread(target=drain, daemon=True)
            self.drainer.start()

    def call(self, operation, **parameters):
        with self.lock:
            self.start()
            self.identity += 1
            identity = self.identity
            request = dumps({'protocol': PROTOCOL, 'id': identity, 'operation': operation, 'parameters': parameters})
            self.metrics['requests'] += 1
            self.metrics['request_bytes'] += len(request) + 1
            outcome = queue.Queue()
            process = self.process
            def exchange():
                try:
                    process.stdin.write(request + b'\n')
                    process.stdin.flush()
                    outcome.put((True, process.stdout.readline(MAX_FRAME + 2)))
                except Exception as exc:
                    outcome.put((False, exc))
            threading.Thread(target=exchange, daemon=True).start()
            try:
                success, raw = outcome.get(timeout=120)
                if not success:
                    raise raw
                if not raw or len(raw) > MAX_FRAME + 1 or not raw.endswith(b'\n'):
                    raise ValueError('Missing, truncated or oversized capability result')
                self.metrics['response_bytes'] += len(raw)
                response = loads(raw)
                if response.get('protocol') != PROTOCOL or response.get('id') != identity or type(response.get('ok')) is not bool:
                    raise ValueError('Capability response identity/version mismatch')
                if not response['ok']:
                    raise CapabilityError(response['error'])
                return response['result']
            except CapabilityError:
                raise
            except Exception as exc:
                diagnostics = {'operation': operation, 'state': 'uncertain', 'exit_code': process.poll(),
                               'stderr': self.stderr.decode('utf-8', errors='replace'), 'stderr_truncated': self.stderr_truncated}
                self.close()
                raise CapabilityError({'code': 'TransportError', 'message': str(exc), 'diagnostics': diagnostics}) from exc


def exact(operation):
    """Workflow's own business arithmetic; independent of format implementations."""
    context = Context(prec=1000, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999, capitals=1, clamp=0, flags=[],
                      traps=[InvalidOperation, DivisionByZero, Overflow, Underflow, Inexact])
    with localcontext(context):
        try:
            return operation()
        except DecimalException as exc:
            raise ValueError('Exact arithmetic exceeds the supported 1000-digit limit') from exc


def bind_api(owner):
    client = owner.client
    class WorkbookView:
        """Public detached cell snapshot plus opaque process-local handle."""
        def __init__(self, result):
            self.handle = result['handle']
            self.refresh(result)
            self.written = None

        def refresh(self, result):
            self.cells = result['cells']
            self.merges = result['merges']
            self.native = result['native']
            self.wps_source_status = result['source_status']
            self.wps_issues = result['source_issues']
            self.wps_source_coverage = result['source_coverage']
            self.wps_input_schema = result['input_schema']

        @classmethod
        def from_bytes(cls, data, source):
            return cls(client.call('open', raw=data, source=str(source)))

        @classmethod
        def create(cls, spec):
            return cls(client.call('create', spec=spec))

        @classmethod
        def load(cls, path):
            from workflow_runtime import snapshot
            return cls.from_bytes(snapshot(str(path))[0], str(path))

        def patch(self, spec):
            self.refresh(client.call('patch', handle=self.handle, spec=spec))

        def calculate(self):
            result = client.call('calculate', handle=self.handle)
            self.refresh(result['view'])
            return result['calculation']

        def save(self, path):
            self.written = client.call('publish', handle=self.handle, output=str(path))

    def region(book, sheet, bounds, **options):
        return client.call('region', handle=book.handle, sheet=sheet, range=bounds, options=options)

    owner.xlsx = SimpleNamespace(Workbook=WorkbookView, address=lambda ref: client.call('address', ref=ref))
    owner.analysis = SimpleNamespace(
        region_from_book=region, column_name=lambda number: client.call('column_name', number=number),
        typed_value=lambda cell: client.call('typed_value', cell=cell),
        validate_query=lambda query, columns, schema: client.call('validate_query', query=query, columns=columns, schema=schema),
        matches=lambda row, filters: client.call('matches', row=row, filters=filters),
        summarize_region=lambda table, query: client.call('summarize', table=table, query=query))
    owner.headers = SimpleNamespace(resolve_column=lambda selector, columns, schema:
                                    client.call('resolve_column', selector=selector, columns=columns, schema=schema))
    owner.match = SimpleNamespace(index_rows=lambda side, nulls: client.call('index_rows', side=side, nulls=nulls))
    owner.math = SimpleNamespace(exact=exact)
    owner.formula = SimpleNamespace(FormulaError=CapabilityError)
    owner.wps = SimpleNamespace(load_result_bytes=lambda raw, _address: WorkbookView(client.call('extract', raw=raw)))
    owner.view_class = WorkbookView
