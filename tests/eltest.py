import csv
import json
from argparse import ArgumentParser, FileType
from importlib.metadata import version
from pathlib import Path

import duckdb
import pytest
from jinja2 import Environment, FileSystemLoader
from json_repair import repair_json
from kfi_fhir_input.kfi_fhir_input import Base
from piper import setup_logging
from piper.datamodel import LinkMLModelLoader
from piper.fhir_consumers import (
    DewrangleJSON,
    ResourceSummary,
    ValidateAgainstIG,
    ValidateResourceBasic,
)
from piper.fixtures import TestFixture
from piper.transform import play
from sqlalchemy import inspect
from sqlalchemy.orm import Session
from yaml import safe_load

data_model = "kfi_fhir_input.kfi_fhir_input"
tbl_prefix = "tgt_fhir_{}"
db_schema_name = ""


class Dataset:
    def __init__(self, datadir="tests/fixtures"):
        """Sets up the configuration, but defers connection if needed,
        or prepares the object."""
        self.datadir = datadir
        self.conn = None

    def __enter__(self):
        """Opens the connection and initializes data when entering the 'with' block."""
        self.conn = duckdb.connect(":memory:")

        # Populate the database
        for file in Path(self.datadir).glob("*.yaml"):
            tablename = file.stem

            with file.open("rt") as f:
                fixtures = safe_load(f)
            self.conn.execute(
                f"CREATE TABLE {tablename} AS SELECT * FROM unnest($data)",
                {"data": fixtures},
            )

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Safely closes the connection automatically when exiting the block."""
        if self.conn:
            self.conn.close()

    def query(self, sql, *args):
        """Example query method."""
        return self.conn.execute(sql, *args).fetchall()


if __name__ == "__main__":
    hosts_file = Path("~/.fhir_hosts").expanduser()
    host_config = None
    if hosts_file.exists():
        host_config = safe_load(hosts_file.open("rt"))

    parser = ArgumentParser(description="Test templates")
    parser.add_argument(
        "-dir", default="tests/fixtures", help="Directory containing test data"
    )
    if host_config:
        parser.add_argument(
            "--host",
            choices=host_config.keys(),
            default=None,
            help="Optional host configuration if '~/.fhir_hosts' exists",
        )
        parser.add_argument(
            "-l",
            "--log-level",
            default="INFO",
            choices=["NOTSET", "DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"],
            help="Log level",
        )
        parser.add_argument(
            "--validate",
            action="store_true",
            help="Validate FHIR resources as they are produced",
        )
        parser.add_argument(
            "--harmony",
            type=FileType("rt"),
            help="File to be used for lookups",
        )
        parser.add_argument(
            "--version", action="version", version=f"%(prog)s {version('piper')}"
        )
    args = parser.parse_args()


    resource_summary = ResourceSummary()
    resource_consumers = [
        ValidateResourceBasic(),
        resource_summary,
        DewrangleJSON(filename="output/fhir/dewrangle.json", buffersize=1000),
    ]
    if args.validate:
        resource_consumers.append(ValidateAgainstIG(hostcfg, args.max_validation_count))

    setup_logging(args.log_level)

    import logging

    if not Path(args.dir).exists():
        import sys
        logging.error(f"Invalid directory, '{args.dir}', found for fixture files. ")
        sys.exit(1)

    with TestFixture(args.dir) as tests:
        play(
            cfg=open("config.yaml", "rt"),
            resource_consumers=resource_consumers,
            resource_summary=resource_summary,
            harmony_file=args.harmony,
            dburi=tests.dburi,
            dbenv="local",
        )
