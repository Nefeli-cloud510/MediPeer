"""Temp helper: locate suitable real Q&A groups for MediPeer demo (read-only)."""

import duckdb

RAW = r"D:\Medipeer\raw_input\major5_stratified_question_samples_answers.parquet"
DEID = r"D:\Medipeer\data_app_chain\runs\t4_20261105T152301Z\deidentified_messages.parquet"

con = duckdb.connect()


def show(rel, label, limit=3):
    print("===", label, "===")
    print(rel.df().to_string(max_colwidth=160))
    print(rel.columns)
    print(rel.dtypes)
    print()


raw = con.sql(f'select * from read_parquet(\'{RAW}\')')
show(raw, "RAW")

deid = con.sql(f'select * from read_parquet(\'{DEID}\')')
show(deid, "DEID")

con.close()
