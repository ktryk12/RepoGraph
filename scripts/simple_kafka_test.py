#!/usr/bin/env python3
"""
Simple Kafka connectivity test
"""

try:
    from confluent_kafka import Producer, Consumer
    print("confluent-kafka is available")

    # Test producer
    producer = Producer({
        'bootstrap.servers': 'localhost:29092',
        'client.id': 'simple-test-producer'
    })

    print("Producer created successfully")

    # Test simple message
    producer.produce('test-topic', key='test', value='hello')
    remaining = producer.flush(timeout=10)

    if remaining == 0:
        print("Message sent successfully")
    else:
        print(f"Flush timeout, {remaining} messages remaining")

    # Test consumer
    consumer = Consumer({
        'bootstrap.servers': 'localhost:29092',
        'group.id': 'simple-test-group',
        'auto.offset.reset': 'latest'
    })

    print("Consumer created successfully")
    consumer.close()
    print("Kafka connectivity test passed")

except ImportError:
    print("confluent-kafka not available - install with: pip install confluent-kafka")
except Exception as e:
    print(f"Kafka connectivity test failed: {e}")