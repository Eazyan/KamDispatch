"use strict";

const form = document.querySelector("#report-form");
const photo = document.querySelector("#photo");
const preview = document.querySelector("#preview");
const photoName = document.querySelector("#photo-name");
const dropZone = document.querySelector("#drop-zone");
const description = document.querySelector("#description");
const count = document.querySelector("#count");
const latitude = document.querySelector("#latitude");
const longitude = document.querySelector("#longitude");
const address = document.querySelector("#address");
const locate = document.querySelector("#locate");
const locationStatus = document.querySelector("#location-status");
const status = document.querySelector("#form-status");
const submit = form.querySelector("[type=submit]");
let previewUrl;
let requestId;

function randomId() {
  // getRandomValues also works when the form is served on a local HTTP network.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)),
    byte => byte.toString(16).padStart(2, "0")).join("");
}

function updatePhoto() {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  const file = photo.files[0];
  const supported = file && ["image/jpeg", "image/png"].includes(file.type);
  const tooLarge = file && file.size > 5 * 1024 * 1024;
  photo.setCustomValidity(file && (!supported || tooLarge)
    ? "Выберите фотографию в формате JPG или PNG размером до 5 МиБ."
    : "");
  photoName.textContent = file ? file.name : "Выберите фото или перетащите сюда";
  previewUrl = supported && !tooLarge ? URL.createObjectURL(file) : undefined;
  preview.hidden = !previewUrl;
  if (previewUrl) preview.src = previewUrl;
  else preview.removeAttribute("src");
  requestId = undefined;
}

photo.addEventListener("change", () => {
  updatePhoto();
  photo.reportValidity();
});
dropZone.addEventListener("dragover", event => {
  event.preventDefault();
  dropZone.classList.add("drag");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag"));
dropZone.addEventListener("drop", event => {
  event.preventDefault();
  dropZone.classList.remove("drag");
  if (!event.dataTransfer.files.length) return;
  const transfer = new DataTransfer();
  transfer.items.add(event.dataTransfer.files[0]);
  photo.files = transfer.files;
  updatePhoto();
  photo.reportValidity();
});

form.addEventListener("input", event => {
  if (event.target.id !== "access-token") requestId = undefined;
});
description.addEventListener("input", () => {
  count.textContent = `${description.value.length} / 880`;
});
locate.addEventListener("click", () => {
  if (!navigator.geolocation) {
    locationStatus.textContent = "Геопозиция недоступна. Введите координаты вручную.";
    return;
  }
  locate.disabled = true;
  locationStatus.textContent = "Определяем координаты…";
  navigator.geolocation.getCurrentPosition(({coords}) => {
    address.value = "";
    latitude.value = coords.latitude.toFixed(6);
    longitude.value = coords.longitude.toFixed(6);
    requestId = undefined;
    locationStatus.textContent = "Координаты добавлены. Проверьте, что это место проблемы.";
    locate.disabled = false;
  }, () => {
    locationStatus.textContent = "Не удалось получить геопозицию. Введите координаты вручную.";
    locate.disabled = false;
  }, {timeout: 10000});
});

const api = "/api/v1/citizens";
const accessField = document.querySelector("#access-field");
const accessToken = document.querySelector("#access-token");

async function loadFormConfig() {
  const response = await fetch(api + "/form-config");
  if (!response.ok) throw new Error("Не удалось загрузить настройки формы.");
  const config = await response.json();
  accessField.hidden = !config.requires_token;
  accessToken.required = config.requires_token;
  return config;
}

loadFormConfig().catch(() => {});

form.addEventListener("submit", async event => {
  event.preventDefault();
  const lat = latitude.value.trim().replace(",", ".");
  const lon = longitude.value.trim().replace(",", ".");
  const text = description.value.trim();
  status.className = "error";
  if (!text || !lat || !lon || !Number.isFinite(Number(lat)) || !Number.isFinite(Number(lon))
      || Math.abs(Number(lat)) > 90 || Math.abs(Number(lon)) > 180) {
    status.textContent = "Введите описание и проверьте широту и долготу.";
    return;
  }
  const file = photo.files[0];
  requestId ??= randomId();
  submit.disabled = true;
  status.className = "";
  status.textContent = "Отправляем обращение…";
  try {
    const config = await loadFormConfig();
    if (config.requires_token && !accessToken.value.trim()) {
      accessToken.focus();
      throw new Error("Укажите токен доступа OIDC.");
    }
    const body = new FormData();
    for (const [key, value] of Object.entries({
      request_id: requestId, description: text,
      latitude: lat, longitude: lon, address: address.value.trim(),
    })) body.append(key, value);
    if (file) body.append("photo", file);
    const tag = document.querySelector("#problem-tag").value;
    if (tag) body.append("tags", tag);
    const headers = {};
    if (config.requires_token) {
      headers.Authorization = "Bearer " + accessToken.value.trim();
    } else {
      const key = "kamdispatch.citizenBrowserId";
      let browserId = localStorage.getItem(key);
      if (!browserId) {
        browserId = randomId();
        localStorage.setItem(key, browserId);
      }
      headers["X-Citizen-Browser-Id"] = browserId;
    }
    const response = await fetch(api + "/reports", {method: "POST", headers, body});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string"
      ? result.detail : "Проверьте данные и повторите отправку.");
    form.reset();
    updatePhoto();
    count.textContent = "0 / 880";
    locationStatus.textContent = "";
    status.className = "success";
    const tags = [...new Set([...result.tags, ...result.suggested_tags])].map(tag => `#${tag}`).join(" ");
    status.textContent = `Обращение №${result.report_number} принято.${tags ? "\n" + tags : ""}`;
  } catch (error) {
    status.className = "error";
    status.textContent = error instanceof TypeError ? "Ошибка соединения. Попробуйте ещё раз." : error.message;
  } finally {
    submit.disabled = false;
  }
});

const dialog = document.querySelector("#location-dialog");
const mapPanel = document.querySelector("#map");
const mapError = document.querySelector("#map-error");
const search = document.querySelector("#address-search");
const query = document.querySelector("#address-query");
const searchButton = search.querySelector("button");
const searchStatus = document.querySelector("#search-status");
const results = document.querySelector("#address-results");
const selectedPlace = document.querySelector("#selected-place");
const selectCenter = document.querySelector("#select-center");
const confirmPlace = document.querySelector("#confirm-place");
const kamchatka = [53.0196, 158.6478];
let map;
let marker;
let place;
let pendingSearch;

function readPoint() {
  const lat = latitude.value.trim().replace(",", ".");
  const lon = longitude.value.trim().replace(",", ".");
  if (!lat || !lon || !validPoint(Number(lat), Number(lon))) return null;
  return {lat: Number(lat), lon: Number(lon), label: address.value.trim()};
}

function validPoint(lat, lon) {
  return Number.isFinite(lat) && Number.isFinite(lon)
    && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
}

function showPoint(point) {
  place = point;
  confirmPlace.disabled = !point;
  selectedPlace.textContent = point
    ? `${point.label ? point.label + " · " : ""}${point.lat.toFixed(6)}, ${point.lon.toFixed(6)}`
    : "Нажмите на карту или выберите найденный адрес.";
  if (!map) return;
  if (marker) marker.remove();
  marker = point ? window.L.circleMarker([point.lat, point.lon], {
    radius: 9, fillColor: "#254ec9", color: "#fff", weight: 3, fillOpacity: 1,
  }).addTo(map) : undefined;
}

function pickMapPoint(latlng) {
  const wrapped = latlng.wrap();
  showPoint({lat: wrapped.lat, lon: wrapped.lng, label: ""});
}

function initializeMap() {
  mapPanel.hidden = !window.L;
  selectCenter.hidden = !window.L;
  if (!window.L) {
    mapError.textContent = "Карта недоступна. Попробуйте поиск по адресу или введите координаты в форме.";
    return;
  }
  if (map) return;
  map = window.L.map(mapPanel).setView(kamchatka, 13);
  map.attributionControl.setPrefix(false);
  const tiles = window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });
  tiles.on("tileerror", () => {
    mapError.textContent = "Карта загрузилась не полностью. Можно воспользоваться поиском по адресу.";
  });
  tiles.on("load", () => {
    if (mapPanel.querySelector(".leaflet-tile-loaded")) mapError.textContent = "";
  });
  tiles.addTo(map);
  map.on("click", event => pickMapPoint(event.latlng));
}

document.querySelector("#open-map").addEventListener("click", () => {
  query.value = address.value;
  results.replaceChildren();
  results.hidden = true;
  searchStatus.textContent = "";
  dialog.showModal();
  initializeMap();
  const point = readPoint();
  showPoint(point);
  if (map) {
    map.invalidateSize({pan: false});
    map.setView(point ? [point.lat, point.lon] : kamchatka, point ? 16 : 13);
  }
  query.focus();
});

for (const id of ["#close-map", "#cancel-map"]) {
  document.querySelector(id).addEventListener("click", () => dialog.close());
}
dialog.addEventListener("close", () => {
  pendingSearch?.abort();
  pendingSearch = undefined;
  searchButton.disabled = false;
});
selectCenter.addEventListener("click", () => pickMapPoint(map.getCenter()));
confirmPlace.addEventListener("click", () => {
  if (!place) return;
  latitude.value = place.lat.toFixed(6);
  longitude.value = place.lon.toFixed(6);
  address.value = place.label;
  requestId = undefined;
  locationStatus.textContent = "Место добавлено к обращению.";
  dialog.close();
});

function resultLabel(properties) {
  const street = [properties.street, properties.housenumber].filter(Boolean).join(", ");
  return [...new Set([street || properties.name, properties.city || properties.locality,
                     properties.state].filter(Boolean))].join(", ");
}

search.addEventListener("submit", async event => {
  event.preventDefault();
  const term = query.value.trim();
  if (term.length < 3) {
    searchStatus.textContent = "Введите хотя бы три символа.";
    return;
  }
  pendingSearch?.abort();
  const controller = new AbortController();
  pendingSearch = controller;
  const timeout = setTimeout(() => controller.abort(), 8000);
  searchButton.disabled = true;
  results.replaceChildren();
  results.hidden = true;
  searchStatus.textContent = "Ищем адрес…";
  const params = new URLSearchParams({q: term, limit: "5", lat: kamchatka[0], lon: kamchatka[1]});
  try {
    const response = await fetch(`https://photon.komoot.io/api/?${params}`, {signal: controller.signal});
    if (!response.ok) throw new Error("Address search failed");
    const data = await response.json();
    if (pendingSearch !== controller) return;
    for (const feature of data.features) {
      if (feature.geometry?.type !== "Point") continue;
      const [lon, lat] = feature.geometry.coordinates;
      const label = resultLabel(feature.properties).slice(0, address.maxLength);
      if (!label || !validPoint(lat, lon)) continue;
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", () => {
        showPoint({lat, lon, label});
        map?.setView([lat, lon], 17);
        results.hidden = true;
        searchStatus.textContent = "";
        confirmPlace.focus();
      });
      item.append(button);
      results.append(item);
    }
    results.hidden = !results.childElementCount;
    searchStatus.textContent = results.hidden
      ? "Адрес не найден. Уточните улицу и населённый пункт или выберите точку на карте."
      : "Выберите адрес из списка.";
  } catch {
    if (pendingSearch === controller) {
      searchStatus.textContent = "Поиск недоступен. Попробуйте позже или выберите точку на карте.";
    }
  } finally {
    clearTimeout(timeout);
    if (pendingSearch === controller) {
      pendingSearch = undefined;
      searchButton.disabled = false;
    }
  }
});
