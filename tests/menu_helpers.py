async def options(send, card, **kwargs):
    return await send(next(data for label, data in card.buttons if label == "⚙️ Опции"), **kwargs)


async def variants(send, card, **kwargs):
    menu = await options(send, card, **kwargs)
    return await send(
        next(data for label, data in menu.buttons if label == "📋 Варианты"), **kwargs
    )


async def export_command(send, card, **kwargs):
    menu = await options(send, card, **kwargs)
    return next(data for label, data in menu.buttons if label == "Экспорт")
