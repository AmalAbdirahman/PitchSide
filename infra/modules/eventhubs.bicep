// Event Hubs namespace with its Kafka endpoint (Standard tier and up) and the two topics of the
// local stack, "events" and "facts". P15 thin slice; written, not deployed.

@description('Namespace name, globally unique.')
param name string
param location string

@description('Partitions per hub. The slice runs one match, so one partition keeps order for free; the design partitions by match_id (docs/architecture/azure.md).')
@minValue(1)
@maxValue(32)
param partitions int = 1

resource ns 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: name
  location: location
  sku: { name: 'Standard', tier: 'Standard', capacity: 1 }
  properties: {
    kafkaEnabled: true
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

resource hubs 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = [for hub in ['events', 'facts']: {
  parent: ns
  name: hub
  properties: {
    partitionCount: partitions
    messageRetentionInDays: 1
  }
}]

// One rule for the worker and the replayer (send and listen). Its connection string is the
// Kafka SASL password; it is read with listKeys() where it is used, never output.
resource rule 'Microsoft.EventHub/namespaces/authorizationRules@2024-01-01' = {
  parent: ns
  name: 'pitchside-live'
  properties: { rights: ['Listen', 'Send'] }
}

output namespaceName string = ns.name
output ruleName string = rule.name
output bootstrap string = '${ns.name}.servicebus.windows.net:9093'
