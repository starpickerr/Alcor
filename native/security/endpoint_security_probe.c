/* Preflight only. Creating an ES client does not protect any process. */
#include <EndpointSecurity/EndpointSecurity.h>
#include <stdio.h>

int main(void) {
    es_client_t *client = NULL;
    es_new_client_result_t result = es_new_client(
        &client, ^(es_client_t *unused_client, const es_message_t *unused_message) {
            (void)unused_client;
            (void)unused_message;
        });
    if (result == ES_NEW_CLIENT_RESULT_SUCCESS) {
        es_delete_client(client);
    }
    const char *name;
    switch (result) {
        case ES_NEW_CLIENT_RESULT_SUCCESS: name = "success"; break;
        case ES_NEW_CLIENT_RESULT_ERR_INVALID_ARGUMENT: name = "invalid_argument"; break;
        case ES_NEW_CLIENT_RESULT_ERR_INTERNAL: name = "internal_error"; break;
        case ES_NEW_CLIENT_RESULT_ERR_NOT_ENTITLED: name = "not_entitled"; break;
        case ES_NEW_CLIENT_RESULT_ERR_NOT_PERMITTED: name = "not_permitted"; break;
        case ES_NEW_CLIENT_RESULT_ERR_NOT_PRIVILEGED: name = "not_privileged"; break;
        case ES_NEW_CLIENT_RESULT_ERR_TOO_MANY_CLIENTS: name = "too_many_clients"; break;
        default: name = "unknown"; break;
    }
    printf("{\"result\":%d,\"reason\":\"%s\",\"status\":\"%s\"}\n",
           (int)result, name, result == ES_NEW_CLIENT_RESULT_SUCCESS ? "available" : "unavailable");
    return 0;
}
