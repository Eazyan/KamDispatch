[out:json][timeout:300];
(
  way["landuse"~"^(industrial|commercial|retail|port)$"](52.9,158.5,53.2,158.8);
  relation["landuse"~"^(industrial|commercial|retail|port)$"](52.9,158.5,53.2,158.8);
  way["industrial"="port"](52.9,158.5,53.2,158.8);
  way["man_made"="pier"](52.9,158.5,53.2,158.8);
);
out geom;
