"""Controlled real-browser integration suite.

Every test in this package drives the production browser stack against a
local loopback fixture server. No test in this package is allowed to reach a
live marketplace: the browser context aborts every request whose host is not
served by the controlled router.
"""
