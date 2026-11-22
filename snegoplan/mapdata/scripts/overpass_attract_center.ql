[out:json][timeout:300];
(
  node["office"](52.9,158.5,53.2,158.8);
  way["office"](52.9,158.5,53.2,158.8);
  node["shop"](52.9,158.5,53.2,158.8);
  way["shop"](52.9,158.5,53.2,158.8);
  node["amenity"~"^(school|kindergarten|university|college|hospital|clinic|marketplace|bank|cafe|restaurant|fuel|place_of_worship|townhall)$"](52.9,158.5,53.2,158.8);
  way["amenity"~"^(school|kindergarten|university|college|hospital|clinic|marketplace|bank|cafe|restaurant|fuel|place_of_worship|townhall)$"](52.9,158.5,53.2,158.8);
  node["building"~"^(commercial|retail|office|industrial|warehouse|public)$"](52.9,158.5,53.2,158.8);
  way["building"~"^(commercial|retail|office|industrial|warehouse|public)$"](52.9,158.5,53.2,158.8);
  node["industrial"="port"](52.9,158.5,53.2,158.8);
);
out center tags;
