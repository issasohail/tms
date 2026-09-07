#!/usr/bin/env python3
"""Compatibility entry point; implementation lives in the invoices app."""

from invoices.iesco_bill_fetch import main


if __name__ == "__main__":
    raise SystemExit(main())
