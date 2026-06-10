import { createContext, useContext, type ReactNode } from 'react'
import type { Semester } from './types'
import { api } from './api'
import { useAsync } from './useAsync'

interface SemesterCtx {
  current: Semester | null
  version: string
  loading: boolean
  reload: () => void
}

const Ctx = createContext<SemesterCtx>({
  current: null,
  version: '',
  loading: true,
  reload: () => {},
})

export function SemesterProvider({ children }: { children: ReactNode }) {
  const { data, loading, reload } = useAsync(() => api.meta(), [])
  return (
    <Ctx.Provider
      value={{
        current: data?.current_semester ?? null,
        version: data?.version ?? '',
        loading,
        reload,
      }}
    >
      {children}
    </Ctx.Provider>
  )
}

export const useSemester = () => useContext(Ctx)
