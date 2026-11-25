"""The server modules the review tools share: the files several processes read and write,
the Call agent presses, the waiter that prints them, the plugin's monitor that runs it, a process
that ends with the one that starts it, and the page's Update of the install.

They are written in tex_mcp_web/shared alone, and scripts/share_page.py copies them byte for
byte into the other tools' <package>/shared, with a manifest their tests check. A copy is never
edited by hand. They import the standard library, aiohttp, psutil and each other, and take what
sets a tool apart as a Tool (tool.py) or as arguments.
"""
