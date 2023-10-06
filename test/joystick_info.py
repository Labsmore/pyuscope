#!/usr/bin/env python3
import pygame


def main():
    pygame.init()
    pygame.joystick.init()

    try:
        joystick = pygame.joystick.Joystick(0)
    except pygame.error:
        print("Joystick not found")
        return

    # This init is required by some systems.
    pygame.joystick.init()
    model = joystick.get_name()
    # version 1.9.6
    print("pygame version", pygame.version.ver, pygame.version.rev)
    # New in pygame 2.0.0dev11.
    try:
        guid = joystick.get_guid()
    except AttributeError:
        raise ImportError(
            "require pygame 2.0.0dev11 or later. try: sudo pip3 install pygame --upgrade"
        )
    print("joystick", model, guid)


if __name__ == "__main__":
    main()
