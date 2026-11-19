# Changes Domus makes to three.js

These files are three.js r187 as published, with one change. Re-apply it when three.js is updated
(`tests/test_serve.py` fails until it is).

- `three.core.js`, `generateUUID()`: the four random 32-bit words come from `crypto.getRandomValues()`
  instead of `Math.random()`. Domus doesn't rely on these IDs for anything secret, but code scanning
  rightly flags a UUID made from a predictable generator, and the browser's generator costs nothing.
  `crypto.getRandomValues()` works on plain-HTTP pages too, unlike `crypto.randomUUID()`.
