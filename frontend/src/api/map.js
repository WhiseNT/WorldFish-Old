import service from './index'

export const mapApi = {
  listMaps: (worldId) => service.get(`/api/world/${worldId}/maps`),
  createMap: (worldId, data) => service.post(`/api/world/${worldId}/maps`, data),
  getMap: (worldId, mapId) => service.get(`/api/world/${worldId}/maps/${mapId}`),
  updateMap: (worldId, mapId, data) => service.put(`/api/world/${worldId}/maps/${mapId}`, data),
  deleteMap: (worldId, mapId) => service.delete(`/api/world/${worldId}/maps/${mapId}`),
  duplicateMap: (worldId, mapId) => service.post(`/api/world/${worldId}/maps/${mapId}/duplicate`),
  setDefaultMap: (worldId, mapId) => service.put(`/api/world/${worldId}/maps/${mapId}/default`),
  updateCell: (worldId, mapId, cellId, data) => service.put(`/api/world/${worldId}/maps/${mapId}/cells/${cellId}`, data),
  batchUpdateCells: (worldId, mapId, data) => service.post(`/api/world/${worldId}/maps/${mapId}/cells/batch`, data),
  searchMap: (worldId, mapId, query) => service.get(`/api/world/${worldId}/maps/${mapId}/search`, { params: { q: query } }),
  getCellNeighbors: (worldId, mapId, cellId) => service.get(`/api/world/${worldId}/maps/${mapId}/cells/${cellId}/neighbors`),
}

export default mapApi
