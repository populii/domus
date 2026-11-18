"""The app: the command line (`cli`), the server (`serve`) and the viewer (`viewer/`, a page and its libraries).

They sit on top of the rest of Domus, which never imports them: the command line reads what a person asks for, hands
the site to the part that does it and prints the result, and the server shows the viewer and runs builds and jobs in
processes of their own.
"""
