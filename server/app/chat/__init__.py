"""Conversational creation (DESIGN.md section 7): one Agent that asks, plans and runs workflows.

store.py      sessions as data/chat/<id>.json
assistant.py  one assistant turn (intent, follow-up questions, generate / edit, start runs)
api.py        /api/chat/* router
"""
