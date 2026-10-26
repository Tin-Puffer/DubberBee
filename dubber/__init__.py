__version__ = "0.4.1"

# Public repo the installed app reads: its releases hold the app zips (in-app update) and the
# models mirror (first-run download). The code repo can be private without breaking either.
# Every installed copy has this baked in — moving it later means one manual reinstall.
RELEASES_REPO = "Tin-Puffer/DubberBee-releases"
