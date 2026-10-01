class BotError(Exception):
    pass


class BotPayloadError(BotError):
    pass


class BotMessageIgnored(BotError):
    pass


class BotSendError(BotError):
    pass
