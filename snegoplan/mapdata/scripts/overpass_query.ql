[out:json][timeout:300];
(
  way["highway"](52.9,158.5,53.2,158.8);
  node["highway"](52.9,158.5,53.2,158.8);
  way["public_transport"](52.9,158.5,53.2,158.8);
  node["public_transport"](52.9,158.5,53.2,158.8);
  relation["route"~"^(bus|trolleybus)$"](52.9,158.5,53.2,158.8);
  way["amenity"="parking"](52.9,158.5,53.2,158.8);
  node["amenity"="parking"](52.9,158.5,53.2,158.8);
  way["parking_lane"](52.9,158.5,53.2,158.8);
  node["barrier"](52.9,158.5,53.2,158.8);
  way["barrier"](52.9,158.5,53.2,158.8);
);
out body geom;
