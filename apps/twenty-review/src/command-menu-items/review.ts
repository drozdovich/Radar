import { defineCommandMenuItem, numberOfSelectedRecords } from 'twenty-sdk/define';
export default defineCommandMenuItem({
  universalIdentifier: '34a17eb4-63be-4a12-b64c-f9d4c449db03',
  label: 'Прочитать и решить', shortLabel: 'Разобрать', isPinned: true,
  availabilityType: 'RECORD_SELECTION',
  availabilityObjectUniversalIdentifier: 'ca40a6f2-8fa1-4eed-8975-a60f944d1e72',
  conditionalAvailabilityExpression: numberOfSelectedRecords === 1,
  frontComponentUniversalIdentifier: '9db0f1a5-7288-4ceb-a80e-783026298170',
});
