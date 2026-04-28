import service from './index'

// 项目管理相关API
export const projectApi = {
  // 创建项目
  createProject: (data) => {
    return service.post('/api/project/create', data)
  },
  
  // 获取项目列表
  getProjects: (params = {}) => {
    return service.get('/api/project/', { params })
  },
  
  // 获取项目详情
  getProject: (projectId) => {
    return service.get(`/api/project/${projectId}`)
  },
  
  // 更新项目
  updateProject: (projectId, data) => {
    return service.put(`/api/project/${projectId}`, data)
  },
  
  // 删除项目
  deleteProject: (projectId) => {
    return service.delete(`/api/project/${projectId}`)
  }
}

export default projectApi