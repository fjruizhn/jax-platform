import axios from 'axios'
import { useJaxStore } from '../store/useJaxStore'

const api = axios.create({
  baseURL: '/api',
  withCredentials: true,
})

api.interceptors.request.use((config) => {
  const token = useJaxStore.getState().token
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  async (err) => {
    if (err.response?.status === 401 && !err.config?._retried) {
      try {
        const { data } = await axios.post('/api/auth/refresh', {}, { withCredentials: true })
        useJaxStore.setState({ token: data.access_token })
        err.config._retried = true
        err.config.headers.Authorization = `Bearer ${data.access_token}`
        return axios(err.config)
      } catch {
        useJaxStore.setState({ token: null, user: null })
      }
    }
    return Promise.reject(err)
  }
)

export default api
