import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  withCredentials: true,
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('jax_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  async (err) => {
    if (err.response?.status === 401) {
      try {
        const { data } = await axios.post('/api/auth/refresh', {}, { withCredentials: true })
        localStorage.setItem('jax_token', data.access_token)
        err.config.headers.Authorization = `Bearer ${data.access_token}`
        return axios(err.config)
      } catch {
        localStorage.removeItem('jax_token')
        window.location.reload()
      }
    }
    return Promise.reject(err)
  }
)

export default api
