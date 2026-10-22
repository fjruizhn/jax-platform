"""Literal invitation-guard fixture from the reviewed b44 schema.

This is intentionally self-contained: database CI checks out a shallow tree, so
the predecessor cannot be loaded with ``git show`` at test time.  The compressed
payload is the four ``CREATE TRIGGER`` statements exactly as they occurred in
``backend/virtual_office/migrations.py`` at ``SOURCE_COMMIT``.  Per-statement
digests below make that provenance independently checkable without Git.
"""
from __future__ import annotations

import base64
import hashlib
import zlib


SOURCE_COMMIT = "b44dd06cc9fe7cc88f8f83566be7c565f76e6675"
SOURCE_PATH = "backend/virtual_office/migrations.py"
_COMPRESSED_STATEMENTS_B64 = (
    "eNrVWFtzozYUft9foTc7U08mmWn7ssYzBMsJXRu8gJPsE0NASdS1wQXhSfrrKwlxERfH9pJt9slCOjo6+r5zszQLqg4EjqVfX0ML6DNgmA6A97rt2OAp9eLAjR4fsY9cHO4w8QiOQjpMUEzAFZyZFgS6YUPLAaYBGpKACgCoajfAMu+o/LVu8CPg3XlCJdJkPBnotr2C0wGgkmx+h+KE7hxPLvlMaczQhnOoOeASzCxzAVqsSpLUC30EEnB3A6lhyXk+5eJAYcor30A1plRiG0d/I5/kAuUnX7/SDdX6RsXQxsNrN4zijbfG/6JAEStsT31NaCZe/ISIm1KoqLqxMmGy8uQZcG6gAWz92lDnwP46tx1GxuD3Py4uLgbAhg5YQNtWr6HrwHtHGTQBLi79gMMAh08gQCFGweAzgNQKfcZ/P33STqE53QYeQTnNq+WUqTiS5gri44k5n0oUCMpL0DORCglUYg/S44lYZLsaNAj3GTZxp2SwHXUy6h7IZMTHb9wb2USX32bz/ER6++FA1TS4dKjAaGDBW/MLH8H7pW7RUQ/Ek9gLE8yHDcqlEFNKU7hnDptXB7rNDTdW8zmXYbs930dbgoICtBbMjg1RtEMhAblm8JepG51SD8iLUcz8LRud82kWqPn+c99Lk8oWZtBelcz3mGMYYlSqFEccrrCSbKg6OdPU3TzLRoXVVS1CuDrDCSiEMzu+0+Cu81iIeD6J4pwRpY06Li+ueMjpEuDZ2bpxq1Mf1U3DvYKqBS13aUEbGoU5Yk/245LoOwrdZy95VmgmLD64pMD+EEMkmoQhIuwkCPwojtG6UJazKc2e9RB02YGcd7TDNO7oYEONpIm3O/zy8M8i6+hoQaf6NGpz58wX0UHoIwl46RqoxetitKNEB+7Dq+R3vdDdA3fMPD8bHs5dnrB/ce6ka3wUQtDLFsevB5ORldXOWirakybUUUoeohduEVOElIGmGhqcz5kWn8Xy2o3RPylKaCZRHGsFR1vvdR15gevj7TNtdNELUVhpHMUoSdfE9aMASRnRgdaCNiK82j/iECfPNAw8oqwczXV0ioOjLpbDP89GWUfF1/TZsL48KZdHja3sdq46nQ4rQjpNv9Ytxf8SLHTNMm2omcaUJrrMUw5hWEDydQUZq58PgvE40D7snbW5Sg+b9tIqixg99H+RiP/utrmr4h2RggA+pXfBLZ0LruUK3tc3c0WRNKsGZ9qlqiBalEr5KHrOWh1P6iW8n/9LNOUWSaerdz6q9/nJrJxYUzjAXVXlxziutHtsY6MJ7K47+GdXHfG34igHkBvvXth+6z/Ou5HV+jcA9/QnoCWG23vxnltxhuXbRHZ1EKPBFM71W2h9c80rmrNvuaf1TPX/EJcfI+D41dZ7Au70qpv1JIe/U+VNTHfdFRrz147iM3/lkbATD1oSnELOj5FoWDKh8juXKFuaMW9Tyu8fh1xckwFOMHltDY38Lats/gqPHw7zrltqlsuXIULQZkvyhyPp0Ugs+VEaEuWiTAptLWOx2mwdG4o3KEm8J9R2ZqUrb33I8tce3mTI1rdWmvXqVv4UKIEgWsUuCBpvZxIMl90wzNS5vQ8H8VLZAlDH7eqW1G94IqYMlLPeHJPb/JZXypg33LKbER5OFYLaSakK8Zn+fLWkhJ9SYegdWJFdNStx9sJZupUH6JXxxTDvDDoqozkrcNy+/U7ZH7DvAl1nyMp5q/WiErG/yD37i0KC4g0OaWneG4jHeFTvluHNJiXewxrJ7cJ/hLiFaQ=="
)
_EXPECTED_SHA256 = (
    "00cdca7bd18074a7f352f5ac8d4601cd00fe3dff5dab887f332f2bd08cfa6e0e",
    "fce554b9f550111db54c94680b4cecd6c24f3d4f8ecbf34515cfbdcf046d3f43",
    "0f5136bda0cc1152c8053044ecacda25241304ebaeb589ddd09d4ad0a150d461",
    "52070bc2680ca624f572d5d2198864d9abba1730edb1de748dd4abca1c0a989d",
)

GUARD_STATEMENTS = tuple(
    zlib.decompress(base64.b64decode(_COMPRESSED_STATEMENTS_B64)).decode("utf-8").split("\n\n")
)
if len(GUARD_STATEMENTS) != 4 or tuple(
    hashlib.sha256(statement.encode("utf-8")).hexdigest()
    for statement in GUARD_STATEMENTS
) != _EXPECTED_SHA256:
    raise RuntimeError("b44 invitation guard fixture failed provenance verification")
