# Product contract (fixture)

A minimal `PRODUCT.md` for `tests/fixtures/product/clean`: the shape the
checker reads, with no claim beyond the two sensors its `do-harness.toml`
declares.

## Contents

- [What it is](#what-it-is)
- [Enforcement sensors](#enforcement-sensors)
- [Retired sensors](#retired-sensors)

## What it is

A fixture root. Nothing is executed; only the document/configuration agreement
is under test.

## Enforcement sensors

| Sensor | Pins |
| --- | --- |
| `pytest` | the fixture's one invariant |
| `product-contract` | that this document and the board agree with the config |
