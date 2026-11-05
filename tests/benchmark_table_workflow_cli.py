"""Maintenance-only synthetic CLI overhead; not model tokens or general performance."""
from decimal import Decimal
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_table_workflow as fixtures
from workflow_runtime import Dependencies, LegacyDependencies
from table_workflow import Workflow


def main():
    fixtures.WorkflowTests.setUpClass()
    case = fixtures.WorkflowTests()
    case.setUp()
    result = {'python': sys.version.split()[0], 'source': 'synthetic 4000 rows; two typed groups; independent expected 2000 each',
              'scope': 'plan including new dependency self-checks and parsing; no model/host measurement', 'backends': {}}
    try:
        case.replace_source([['机构', '金额']] + [['甲' if index % 2 == 0 else '乙', 1] for index in range(4000)])
        for name, constructor in [('legacy', LegacyDependencies), ('cli', Dependencies)]:
            durations = []
            frames = []
            for _ in range(3):
                started = time.perf_counter()
                dependency = constructor(str(fixtures.XLSX_ROOT), str(fixtures.ET_ROOT))
                try:
                    book, audit = Workflow(dependency).prepare(case.plan)
                    assert book.cells['Data']['B2']['value'] == Decimal(2000)
                    assert book.cells['Data']['B3']['value'] == Decimal(2000)
                    assert audit['status'] == 'validated'
                    if name == 'cli':
                        frames.append(dict(dependency.client.metrics))
                finally:
                    dependency.close()
                durations.append(time.perf_counter() - started)
            result['backends'][name] = {'seconds': durations, 'median_seconds': statistics.median(durations), 'transport': frames}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        case.doCleanups()


if __name__ == '__main__':
    main()
