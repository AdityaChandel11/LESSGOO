# Google attribution logo — one file has to be added by hand

The Google basemap (`MAPS_MODE=google`, which serves tiles from the Map Tiles
API) may only be displayed with Google's own logo beside the copyright text.
That logo is Google's asset, published under the Maps Platform attribution and
brand guidelines, and is not ours to commit to a public repository. It is the
one file in this project that has to be fetched by a person.

## What to add

Download the light-background Google logo from the Google Maps Platform
attribution guidelines (the same asset set used for "powered by Google"
attributions) and save it here as:

    public/google-attribution/google_on_white.png

Nothing else needs changing: `frontend/src/googleTiles.ts` loads exactly that
path, and Vite copies `public/` into the build as-is.

## Until it is added

`loadGoogleLogo()` fails, and the map deliberately falls back to OpenStreetMap
with the banner "Showing OpenStreetMap — the Google attribution logo is missing
from this build". That refusal is the point: rendering Google's imagery without
the attribution it requires would be a licensing problem that nobody would
notice, whereas an OpenStreetMap basemap is merely a visible difference.

The copyright *text* beside the logo is already handled in code: it comes from
the Map Tiles API's own `/tile/v1/viewport` endpoint and is refreshed on every
pan, which is what Google requires for the area on screen.
