"""Trader ledger (sales assistant for traders and small manufacturers).

A trader keeps a catalogue (cost price, units in stock, set selling price)
and records each sale or restock by voice or text. All money maths is done
in code here (``ledger``); N-ATLaS only helps read the message (``parse``),
and every number it returns must be a number the trader actually said.

Modules:
- ``money``: read numbers and naira amounts from text, format them.
- ``ledger``: pure profit, discount, stock and summary maths.
- ``parse``: turn a trader's message into an action (sale, restock, ...).
- ``statement``: the lender statement (PDF and CSV), plain Python.
- ``flow``: the conversation (ask, read back, confirm, save).
"""
