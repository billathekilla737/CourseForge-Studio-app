"""Attendance for one course: who was here, tardy, absent, or excused.

The book is instructor records, keyed by Canvas user id. It is not a grade,
and nothing in this package writes the gradebook. A copy travels with the
other instructor-owned state (see statesync) so a second computer sees the
same marks. Names are not stored in the book.
"""
