import { create } from 'zustand'

type Access = {
  hasAccess: boolean
  trialExpiresAt: string | null
}
type State = {
  userId: number | null
  access: Access
  setAccess: (a: Access) => void
  setUserId: (id: number | null) => void
}

export const useAuth = create<State>((set) => ({
  userId: null,
  access: { hasAccess: false, trialExpiresAt: null },
  setAccess: (a) => set({ access: a }),
  setUserId: (id) => set({ userId: id }),
}))
