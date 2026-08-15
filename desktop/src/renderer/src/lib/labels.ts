import type { ApprovalState } from '@shared/ide'

export const APPROVAL_TEXT: Record<ApprovalState, string> = {
  'not-requested': 'Not requested',
  pending: 'Pending review',
  approved: 'Approved',
  rejected: 'Rejected'
}
