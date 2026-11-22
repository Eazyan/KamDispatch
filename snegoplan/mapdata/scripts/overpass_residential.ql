[out:json][timeout:300];
(
  way["building"~"^(apartments|residential|house|detached|dormitory|semidetached_house|terrace|yes)$"](52.9,158.5,53.2,158.8);
);
out geom;
