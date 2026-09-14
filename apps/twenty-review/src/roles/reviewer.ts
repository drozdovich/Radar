import { defineApplicationRole } from 'twenty-sdk/define';

export default defineApplicationRole({
  universalIdentifier: '8628d978-bfe6-4169-b031-2a4c77834d15',
  label: 'Radar reviewer',
  canReadAllObjectRecords: false,
  canUpdateAllObjectRecords: false,
  canSoftDeleteAllObjectRecords: false,
  canDestroyAllObjectRecords: false,
  objectPermissions: [{
    objectUniversalIdentifier: 'ca40a6f2-8fa1-4eed-8975-a60f944d1e72',
    canReadObjectRecords: true,
    canUpdateObjectRecords: true,
    canSoftDeleteObjectRecords: false,
    canDestroyObjectRecords: false,
  }, ...[
    'aeda2fb5-7252-480b-b038-2a3492662e8d', // Ивенты
    '20202020-b374-4779-a561-80086cb2e17f', // Company
    '20202020-e674-48e5-a542-72570eee7213', // Person
    '20202020-9549-49dd-b2b2-883999db8938', // Opportunity
    '20202020-0b00-45cd-b6f6-6cd806fc6804', // Note
    '20202020-fff0-4b44-be82-bda313884400', // NoteTarget
  ].map(objectUniversalIdentifier => ({ objectUniversalIdentifier, canReadObjectRecords: true,
    canUpdateObjectRecords: true, canSoftDeleteObjectRecords: false, canDestroyObjectRecords: false }))],
});
