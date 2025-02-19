import multiprocessing
import re
from queue import Empty
from typing import List

import polars as pl
import pyarrow.parquet as pq
from pyarrow import Table


def __writer_process__(
    export_path, queue: multiprocessing.Queue, basename_template: str
):
    while True:
        try:
            table = queue.get()
        except Empty:
            continue

        if not isinstance(table, Table):
            break

        pq.write_to_dataset(
            root_path=export_path,
            compression="snappy",
            table=table,
            basename_template=basename_template,
        )


class DataExporter:
    def __init__(
        self,
        export_path,
        export_include_source_columns: bool,
        basename_template: str = None,
        process_count: int = multiprocessing.cpu_count(),
    ):
        self.__export_path__ = export_path
        self.__export_include_source_columns__ = export_include_source_columns
        if basename_template and not re.search(r"-{i}\.parquet$", basename_template):
            basename_template += "-{i}.parquet"
        self.__basename_template__ = basename_template
        self.__queue__ = queue = multiprocessing.Queue(maxsize=process_count)

        self.__processes__ = processes = []
        for _ in range(process_count):
            p = multiprocessing.Process(
                target=__writer_process__,
                kwargs={
                    "queue": queue,
                    "export_path": self.__export_path__,
                    "basename_template": self.__basename_template__,
                },
            )
            processes.append(p)

        # start processes
        [p.start() for p in processes]

    def __del__(self):
        if self.__queue__:
            self.close()

    def close(self):
        for _ in range(len(self.__processes__)):
            self.__queue__.put(None)

        for p in self.__processes__:
            p.join()
            p.close()

        self.__queue__.close()
        del self.__queue__
        self.__queue__ = None

    def collect(self, lf: pl.LazyFrame, collected_columns: List[str]):
        if not self.__export_include_source_columns__:
            # collect only applied columns
            lf = lf.select(collected_columns)

        # compute final dataframe
        df: pl.DataFrame = lf.collect(streaming=True)
        self.__queue__.put(df.to_arrow())
