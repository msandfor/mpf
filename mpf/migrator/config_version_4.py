import os
import re
from copy import deepcopy

from ruamel import yaml
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from mpf.migrator.migrator import VersionMigrator
from mpf.core.rgb_color import named_rgb_colors, RGBColor

from mpf.file_interfaces.yaml_interface import YamlInterface, \
    MpfRoundTripLoader


class V4Migrator(VersionMigrator):

    config_version = 4

    # These sections are in the order they're processed

    renames = '''
    - old: sound_system|initial_volume
      new: master_volume
    - old: tocks_per_sec
      new: speed
    - old: fonts
      new: text_styles
    - old: movies
      new: videos
    - old: lights_when_disabled
      new: show_when_disable
    - old: num_repeats
      new: loops
    - old: repeat
      new: loops
    - old: loop
      new: loop
    - old: game|allow start with loose balls
      new: allow_start_with_loose_balls
    '''

    moves = '''
    - old: window|frame
      new: window|borderless
    - old: window|quit_on_close
      new: window|exit_on_escape
    '''

    deprecations = '''
    - timing
    - plugins
    - sound_system|volume_steps
    - sound_system|stream
    - window|elements|__list__|pixel_spacing
    - window|fps
    - dmd
    - displays|dmd|physical
    - displays|dmd|type

    # everything from here down is old than v3, but I saw them in some configs
    # so figured we can get rid of them now too
    - machine_flow
    - machineflow
    '''

    additions = '''
    sound_system:
      enabled: True
    '''

    slides = dict()
    displays = dict()
    default_display = None
    color_dmd = False
    WIDTH = 800
    HEIGHT = 600
    MAIN_CONFIG_FILE = 'config.yaml'

    def __init__(self, file_name, file_contents):
        super().__init__(file_name, file_contents)
        self.created_slide_player = False

    @classmethod
    def _get_slide_name(cls, display):
        if display not in cls.slides:
            cls.slides[display] = 0

        cls.slides[display] += 1

        if display:
            return '{}_slide_{}'.format(display, cls.slides[display])
        else:
            return 'slide_{}'.format(cls.slides[display])

    @classmethod
    def _add_display(cls, name, w, h):
        cls.log.debug("Detected display '%s' (%sx%s)", name, w, h)
        cls.displays[name] = (w, h)

    def _do_custom(self):
        self._set_dmd_type()
        self._migrate_window()
        self._create_display_from_dmd()
        self._migrate_physical_dmd()
        self._set_default_display()
        self._migrate_slide_player()
        self._create_window_slide()
        self._migrate_sound_system()
        self._migrate_fonts()
        self._migrate_asset_defaults()
        self._migrate_animation_assets()
        self._migrate_show_player()
        self._migrate_light_player()
        self._migrate_shot_profiles()
        self._migrate_light_scripts()
        self._migrate_assets('images')
        self._migrate_assets('videos')
        self._migrate_assets('sounds')

    def _migrate_window(self):
        # Create a display from the window
        if 'window' in self.fc:
            self.log.debug("Converting window: section")
            if 'displays' not in self.fc:
                self.fc['displays'] = CommentedMap()
            self.fc['displays']['window'] = CommentedMap()

            self.fc['displays']['window']['height'] = self.fc['window'].get(
                'height', 600)
            self.fc['displays']['window']['width'] = self.fc['window'].get(
                'width', 800)
            self._add_display('window', self.fc['displays']['window']['width'],
                              self.fc['displays']['window']['height'])
            V4Migrator.default_display = 'window'

            try:  # old setting was 'frame', so we need to flip it
                self.fc['window']['borderless'] = not self.fc['window']['borderless']
            except KeyError:
                pass

    def _create_display_from_dmd(self):
        if 'dmd' in self.fc:
            self.log.debug("Converting dmd: to displays:dmd:")
            if 'displays' not in self.fc:
                self.log.debug("Creating 'displays:' section")
                self.fc['displays'] = CommentedMap()

            V4Migrator.default_display = 'dmd'

            self.log.debug("Creating 'displays:dmd: section")
            self.fc['displays']['dmd'] = CommentedMap()

            self.fc['displays']['dmd'].update(self.fc['dmd'])
            self._add_display('dmd', self.fc['dmd']['width'],
                              self.fc['dmd']['height'])

    def _migrate_physical_dmd(self):
        if ('dmd' in self.fc and 'physical' in self.fc['dmd'] and
                self.fc['dmd']['physical']):

            self.log.debug("Converting physical dmd: settings")

            YamlInterface.del_key_with_comments(self.fc['dmd'], 'physical',
                                                self.log)
            YamlInterface.del_key_with_comments(self.fc['dmd'], 'fps',
                                                self.log)

            if 'type' in self.fc['dmd'] and self.fc['dmd']['type'] == 'color':
                # physical color DMD
                YamlInterface.del_key_with_comments(self.fc['dmd'], 'type',
                                                    self.log)
                YamlInterface.rename_key('dmd', 'physical_rgb_dmd', self.fc,
                                         self.log)

            else:  # physical mono DMD
                YamlInterface.del_key_with_comments(self.fc['dmd'], 'type',
                                                    self.log)

                YamlInterface.rename_key('dmd', 'physical_dmd', self.fc,
                                         self.log)

            YamlInterface.del_key_with_comments(self.fc['displays']['dmd'],
                                                'physical', self.log)
            YamlInterface.del_key_with_comments(self.fc['displays']['dmd'],
                                                'shades', self.log)
            YamlInterface.del_key_with_comments(self.fc['displays']['dmd'],
                                                'fps', self.log)

    def _set_dmd_type(self):
        if 'dmd' in self.fc and 'type' in self.fc['dmd']:
            if self.fc['dmd']['type'].lower() == 'color':
                V4Migrator.color_dmd = True
                self.log.debug("Detected settings for color DMD")

    def _set_default_display(self):
        if 'displays' not in self.fc or len(self.fc['displays']) == 1:
            return

        if V4Migrator.default_display in self.fc['displays']:
            self.fc['displays'][V4Migrator.default_display]['default'] = True

    def _migrate_slide_player(self):
        if 'slide_player' in self.fc:
            self.log.debug("Converting slide_player: entries to slides:")

            self.fc['slides'] = CommentedMap()
            new_slide_player = CommentedMap()

            for event, elements in self.fc['slide_player'].items():
                self.log.debug("Converting '%s' display_elements to widgets",
                               event)

                display = None
                transition = None
                expire = None

                if isinstance(elements, dict):
                    elements = [elements]

                for element in elements:
                    if 'display' in element:
                        self.log.debug("Converting display: to target:")
                        display = element['display']
                        del element['display']
                    if 'transition' in element:
                        transition = (element['transition'],
                                      element.ca.items.get('transition', None))
                        del element['transition']
                    if 'expire' in element:
                        del element['expire']

                elements = self._migrate_elements(elements, display)

                slide = V4Migrator._get_slide_name(display)

                new_slide_player[event] = CommentedMap()
                new_slide_player[event][slide] = CommentedMap()
                self.log.debug("Adding slide:%s to slide_player:%s", slide,
                               event)

                if transition:
                    self.log.debug("Moving transition: from slide: to "
                                   "slide_player:")
                    new_slide_player[event][slide]['transition'] = transition[0]
                    new_slide_player[event][slide].ca.items['transition'] = (
                        transition[1])

                if display:
                    self.log.debug("Setting slide_player:target: to '%s'",
                                   display)
                    new_slide_player[event][slide]['target'] = display

                if expire:
                    self.log.debug("Setting slide_player:expire: to '%s'",
                                   display)

                if not new_slide_player[event][slide]:
                    new_slide_player[event] = slide

                self.log.debug("Creating slide: '%s' with %s migrated "
                               "widget(s)", slide, len(elements))
                self.fc['slides'][slide] = elements

            self.fc['slide_player'] = new_slide_player

    def _create_window_slide(self):
        if 'window' in self.fc and 'elements' in self.fc['window']:
            elements = self.fc['window']['elements']

            if isinstance(elements, dict):
                elements = [elements]

            if 'slides' not in self.fc:
                self.log.debug("Creating 'slides:' section")
                self.fc['slides'] = CommentedMap()

            slide_name = V4Migrator._get_slide_name('window')

            self.log.debug("Creating slide: %s with %s display widget(s) from "
                           "the old window: config", slide_name, len(elements))

            self.log.debug("Adding '%s' slide", slide_name)
            self.fc['slides'][slide_name] = CommentedMap()
            self.fc['slides'][slide_name] = (
                self._migrate_elements(elements, 'window'))

            YamlInterface.del_key_with_comments(self.fc['window'], 'elements',
                                                self.log)

            if 'slide_player' not in self.fc:
                self.fc['slide_player'] = CommentedMap()
                self.log.debug("Creating slide_player: section")

            self.log.debug("Creating slide_player:machine_reset_phase3: entry"
                           "to show slide '%s' on boot", slide_name)
            self.fc['slide_player']['machine_reset_phase_3'] = CommentedMap()
            self.fc['slide_player']['machine_reset_phase_3'][slide_name] = \
                CommentedMap()
            self.fc['slide_player']['machine_reset_phase_3'][slide_name][
                'target'] = 'window'

    def _migrate_sound_system(self):
        # convert stream track to regular track
        try:
            stream_track_name = self.fc['sound_system']['stream']['name']
            self.fc['sound_system']['tracks'][stream_track_name] = (
                CommentedMap())
            self.fc['sound_system']['tracks'][stream_track_name]['volume'] = 0.5
            self.fc['sound_system']['tracks'][stream_track_name][
                 'simultaneous_sounds'] = 1
            self.log.debug('Converting stream: audio track to normal track')
        except KeyError:
            pass

        try:
            old_buffer = self.fc['sound_system']['buffer']
        except KeyError:
            old_buffer = None

        try:
            self.fc['sound_system']['buffer'] = 2048
            if old_buffer:
                self.fc['sound_system'].yaml_add_eol_comment(
                    'previous value was {}'.format(old_buffer), 'buffer')
                self.log.debug("Setting sound_system:buffer: to '2048'. "
                               "(Was %s)", old_buffer)
        except KeyError:
            pass

    def _migrate_fonts(self):
        # Fonts to text_styles was already renamed, now update contents
        if 'text_styles' in self.fc:
            self.log.debug("Converting text_styles: from the old fonts: "
                           "settings")
            for settings in self.fc['text_styles'].values():
                YamlInterface.rename_key('size', 'font_size', settings, self.log)
                YamlInterface.rename_key('file', 'font_name', settings, self.log)

                if 'font_name' in settings:
                    self.log.debug("Converting font_name: from file to name")
                    settings['font_name'] = os.path.splitext(
                        settings['font_name'])[0]

        if self.base_name == V4Migrator.MAIN_CONFIG_FILE:
            if 'text_styles' not in self.fc:
                self.log.debug("Creating old default font settings as "
                               "text_styles: section")
                self.fc['text_styles'] = self._get_old_default_text_styles()

            else:
                self.log.debug("Merging old default font settings into "
                               "text_styles: section")
                self.fc['text_styles'] = (
                    self._get_old_default_text_styles().update(
                        self.fc['text_styles']))

    def _migrate_asset_defaults(self):
        # convert asset_defaults to assets:
        if 'asset_defaults' in self.fc:
            self.log.debug('Renaming key: asset_defaults -> assets:')
            YamlInterface.rename_key('asset_defaults', 'assets', self.fc,
                                     self.log)

            assets = self.fc['assets']

            if 'animations' in assets:
                self.log.debug("Converting assets:animations to assets:images")
                if 'images' in assets:
                    self.log.debug("Merging animations: into current "
                                   "asset:images:")
                    YamlInterface.copy_with_comments(assets, 'animations',
                                                     assets, 'images',
                                                     True, self.log)
                else:
                    YamlInterface.rename_key('animations', 'images', assets,
                                             self.log)
                YamlInterface.del_key_with_comments(self.fc, 'animations',
                                                    self.log)

            if 'movies' in assets:
                YamlInterface.rename_key('movies', 'videos', assets, self.log)

            if 'images' in assets:
                self.log.debug("Converting assets:images:")

                for settings in assets['images'].values():
                    YamlInterface.del_key_with_comments(settings, 'target',
                                                        self.log)

            if 'sounds' in assets:
                self.log.debug("Converting assets:sounds:")

                for asset, settings in assets['sounds'].items():
                    pass  # todo

    def _migrate_animation_assets(self):
        if 'animations' in self.fc:
            self.log.debug("Converting assets:animations to assets:images")
            if 'images' in self.fc:
                self.log.debug("Merging animations: into current "
                               "asset:images:")

                YamlInterface.copy_with_comments(self.fc, 'animations',
                                                 self.fc, 'images',
                                                 True, self.log)

            else:
                YamlInterface.rename_key('animations', 'images', self.fc,
                                         self.log)

    def _migrate_show_player(self):
        if 'show_player' not in self.fc:
            return

        temp_show_player = deepcopy(self.fc['show_player'])

        self.log.debug("Migrating show_player: section")

        for event, actions in self.fc['show_player'].items():
            if not isinstance(actions, list):
                actions = [actions]
            this_events_shows = CommentedMap()

            for action in actions:
                if 'show' in action:
                    show_name = action.pop('show')
                    this_events_shows[show_name] = action

            temp_show_player[event] = this_events_shows

        del self.fc['show_player']  # do not want to delete comments
        for event, shows in temp_show_player.items():
            self._add_to_show_player(event, shows)

    def _migrate_light_player(self):
        # light_player: section in v3 was used for both playing light scripts
        # and playing shows

        if 'light_player' not in self.fc:
            return

        self.log.debug("Migrating light_player: section")

        for event, actions in self.fc['light_player'].items():
            if not isinstance(actions, list):
                actions = [actions]
            this_events_shows = CommentedMap()

            for i, action in enumerate(actions):

                if 'show' in action:
                    show_name = action.pop('show')

                elif 'script' in action:
                    show_name = action.pop('script')

                elif 'key' in action:
                    show_name = action.pop('key')

                else:
                    continue

                this_events_shows[show_name] = action

            self._add_to_show_player(event, this_events_shows)

        YamlInterface.del_key_with_comments(self.fc, 'light_player', self.log)

    def _migrate_shot_profiles(self):
        if 'shot_profiles' not in self.fc:
            return

        for name, settings in self.fc['shot_profiles'].items():
            if 'states' in settings:
                for i, state_settings in enumerate(settings['states']):
                    if 'loops' in state_settings and state_settings['loops']:
                        state_settings['loops'] = -1
                    YamlInterface.rename_key('light_script', 'show',
                                             state_settings)

    def _add_to_show_player(self, event, show_dict):
        for show, settings in show_dict.items():
            if 'loops' in settings:
                if settings['loops']:
                    settings['loops'] = -1
                else:
                    settings['loops'] = 0

        if 'show_player' not in self.fc:
            self.log.debug("Creating show_player: section")
            self.fc['show_player'] = CommentedMap()

        if event not in self.fc['show_player']:
            self.log.debug("Updating show_player: content")
            self.fc['show_player'][event] = CommentedMap()
            self.fc['show_player'].ca.items[event] = show_dict.ca.items

        try:
            self.fc['show_player'][event].update(show_dict)
            self.fc['show_player'].ca.items[event] = (
                self.fc['light_player'].ca.items[event])
        except KeyError:
            pass

    def _migrate_assets(self, section_name):
        if section_name in self.fc:

            keys_to_keep = set(self.mpf_config_spec[section_name].keys())
            empty_entries = set()

            self.log.debug("Converting %s: section", section_name)

            if self.fc[section_name]:

                for name, settings in self.fc[section_name].items():
                    self.log.debug("Converting %s:%s:", section_name, name)
                    if isinstance(settings, dict):
                        keys = set(settings.keys())
                        keys_to_remove = keys - keys_to_keep

                        for key in keys_to_remove:
                            YamlInterface.del_key_with_comments(settings, key,
                                                                self.log)

                    if not settings:
                        self.log.debug("%s:%s: is now empty. Will remove it.",
                                       section_name, name)
                        empty_entries.add(name)

                for name in empty_entries:
                    YamlInterface.del_key_with_comments(self.fc[section_name],
                                                        name, self.log)

            if len(self.fc[section_name]) == 0:
                self.log.debug("%s: is now empty. Will remove it.",
                               section_name)
                YamlInterface.del_key_with_comments(self.fc, section_name,
                                                    self.log)

    def _migrate_elements(self, elements, display=None):
        # takes a list of elements, returns a list of widgets
        if isinstance(elements, dict):
            elements = [elements]

        non_widgets = list()

        for i, element in enumerate(elements):
            elements[i] = self._element_to_widget(element, display)
            if not elements[i]:
                non_widgets.append(elements[i])

        for nw in non_widgets:
            elements.remove(nw)
            # todo do something with these?

        return elements

    def _element_to_widget(self, element, display):
        # takes an element dict, returns a widget dict

        # Figure out which display we're working with so we can get the
        # size to update the positions later. This could be target or
        # display, since this meth is called from a few different places

        if 'target' in element:
            display = element['target']
        elif 'display' in element:
            display = element['display']

        if not display:
            display = V4Migrator.default_display

        if display:
            width, height = V4Migrator.displays[display]
        elif V4Migrator.WIDTH and V4Migrator.HEIGHT:
            width = V4Migrator.WIDTH
            height = V4Migrator.HEIGHT
        else:
            raise ValueError("Unable to auto-detect display with and height. "
                             "Run the migrator again with the -h and -w "
                             "options to manually specific width and height")

        try:
            element_type = element['type'].lower()

        except KeyError:
            return False

        type_map = dict(virtualdmd='dmd',
                        text='text',
                        shape='shape',
                        animation='animation',
                        image='image',
                        movie='video',
                        character_picker='character_picker',
                        entered_chars='entered_chars')

        # Migrate the element type
        element['type'] = type_map[element_type]

        self.log.debug('Converting "%s" display_element to "%s" widget',
                       element_type, element['type'])

        # Migrate layer
        YamlInterface.rename_key('layer', 'z', element, self.log)
        YamlInterface.rename_key('h_pos', 'anchor_x', element, self.log)
        YamlInterface.rename_key('v_pos', 'anchor_y', element, self.log)
        YamlInterface.rename_key('font', 'style', element, self.log)
        YamlInterface.rename_key('shade', 'brightness', element, self.log)
        YamlInterface.del_key_with_comments(element, 'pixel_spacing', self.log)
        YamlInterface.del_key_with_comments(element, 'antialias', self.log)
        YamlInterface.del_key_with_comments(element, 'thickness', self.log)
        YamlInterface.del_key_with_comments(element, 'bg_shade', self.log)
        YamlInterface.del_key_with_comments(element, 'slide', self.log)

        if element_type == 'text':
            YamlInterface.rename_key('size', 'font_size', element, self.log)

        if element_type != 'dmd':
            YamlInterface.del_key_with_comments(element, 'bg_color', self.log)

        if element_type == 'virtualdmd' and V4Migrator.color_dmd:
            YamlInterface.del_key_with_comments(element, 'pixel_color',
                                                self.log)
            self.log.debug('Changing widget type from "dmd" to "color_dmd"')
            element['type'] = 'color_dmd'

        if 'y' in element:
            old_y = element['y']
            element['y'] *= -1
        else:
            old_y = 'None'

        if 'anchor_y' not in element and 'y' in element:
            element['anchor_y'] = 'top'

        try:
            if 'anchor_y' not in element:
                if element['y'] < 0:
                    element['y'] = 'bottom{}'.format(element['y'])
                else:
                    element['y'] = 'bottom+{}'.format(element['y'])

            elif element['anchor_y'] in ('middle', 'center'):
                if element['y'] < 0:
                    element['y'] = 'middle{}'.format(element['y'])
                else:
                    element['y'] = 'middle+{}'.format(element['y'])

            elif element['anchor_y'] == 'top':
                if element['y'] < 0:
                    element['y'] = 'top{}'.format(element['y'])
                else:
                    element['y'] = 'top+{}'.format(element['y'])

            elif element['anchor_y'] == 'bottom':
                if element['y'] < 0:
                    element['y'] = 'bottom{}'.format(element['y'])
                else:
                    element['y'] = 'bottom+{}'.format(element['y'])

            self.log.debug("Changing y:%s to y:%s (Based on anchor_y:%s"
                           "and %s height:%s)", old_y, element['y'],
                           element['anchor_y'], display, height)

        except KeyError:
            pass

        try:
            if element['anchor_y'] in ('middle', 'center'):
                YamlInterface.del_key_with_comments(element, 'anchor_y',
                                                    self.log)

        except KeyError:
            pass

        if ('anchor_y' in element and not 'y' in element and
                element['anchor_y'] != 'middle'):
            element['y'] = element['anchor_y']


        if 'x' in element:
            old_x = element['x']
        else:
            old_x = 'None'

        if 'anchor_x' not in element and 'x' in element:
            element['anchor_x'] = 'left'

        try:
            if 'anchor_x' not in element:
                if element['x'] < 0:
                    element['x'] = 'left{}'.format(element['x'])
                else:
                    element['x'] = 'left+{}'.format(element['x'])

            elif element['anchor_x'] in ('middle', 'center'):
                if element['x'] < 0:
                    element['x'] = 'center{}'.format(element['x'])
                else:
                    element['x'] = 'center+{}'.format(element['x'])

            elif element['anchor_x'] == 'right':
                if element['x'] < 0:
                    element['x'] = 'right{}'.format(element['x'])
                else:
                    element['x'] = 'right+{}'.format(element['x'])

            elif element['anchor_x'] == 'left':
                if element['x'] < 0:
                    element['x'] = 'left{}'.format(element['x'])
                else:
                    element['x'] = 'left+{}'.format(element['x'])

            self.log.debug("Changing x:%s to x:%s (Based on anchor_x:%s"
                           "and %s width:%s)", old_x, element['x'],
                           element['anchor_x'], display, width)

        except KeyError:
            pass

        try:
            if element['anchor_x'] in ('middle', 'center'):
                YamlInterface.del_key_with_comments(element, 'anchor_x',
                                                    self.log)
        except KeyError:
            pass

        if ('anchor_x' in element and not 'x' in element and
                element['anchor_x'] != 'center'):
            element['x'] = element['anchor_x']

        if element_type == 'animation':
            element = self._migrate_animation(element)
        elif element_type == 'shape':
            element = self._migrate_shape(element)

        if 'decorators' in element:
            element = self._migrate_decorators(element, 'decorators',
                                               'animations')

        if 'cursor_decorators' in element:
            element = self._migrate_decorators(element, 'cursor_decorators',
                                               'cursor_animations')

        if 'color' in element:
            element['color'] = self._get_color(element['color'])

        self._convert_tokens(element)

        return element

    def _get_color(self, color):
        color_tuple = RGBColor.hex_to_rgb(color)

        for color_name, val in named_rgb_colors.items():
            if color_tuple == val:
                self.log.debug("Converting hex color '%s' to named color "
                               "'%s'", color, color_name)
                return color_name

        return color

    def _migrate_shape(self, element):
        if element['shape'] == 'box':
            self.log.debug("Converting 'box' display_element to 'rectangle' "
                           "widget")
            element['type'] = 'rectangle'
            del element['shape']

        elif element['shape'] == 'line':
            self.log.debug("Converting 'line' display_element to 'line' widget")
            element['type'] = 'line'
            del element['shape']

            element['points'] = (element.get('x', 0),
                                 element.get('y', 0),
                                 element.get('x', 0) + element['height'],
                                 element.get('y', 0) + element['width'])
        return element

    def _migrate_animation(self, element):
        self.log.debug("Converting 'animation' display_element to animated "
                       "'image' widget")
        element['type'] = 'image'
        YamlInterface.rename_key('play_now', 'auto_play', element, self.log)
        YamlInterface.rename_key('animation', 'image', element, self.log)

        element.pop('drop_frames', None)

        self.log.debug('Converting animated image loops: setting')
        if element['loops']:
            element['loops'] = -1
        else:
            element['loops'] = 0

        return element

    def _migrate_decorators(self, element, old_key, new_key):
        self.log.debug("Converting display_element blink decorator to widget "
                       "animation")
        decorator = element[old_key]

        element[new_key] = CommentedMap()
        element[new_key]['entrance'] = CommentedSeq()

        on_dict = CommentedMap()
        on_dict['property'] = 'opacity'
        on_dict['value'] = 1
        on_dict['duration'] = str(decorator.get('on_secs', .5)) + 's'

        element[new_key]['entrance'].append(on_dict)

        off_dict = CommentedMap()
        off_dict['property'] = 'opacity'
        off_dict['value'] = 0
        off_dict['duration'] = str(decorator.get('off_secs', .5)) + 's'
        off_dict['repeat'] = True

        element[new_key]['entrance'].append(off_dict)

        del element[old_key]

        return element

    def _migrate_light_scripts(self):
        if 'light_scripts' not in self.fc:
            return

        YamlInterface.rename_key('light_scripts', 'shows', self.fc, self.log)

        for show_name, show_contents in self.fc['shows'].items():
            self._convert_tocks_to_time(show_contents)

            for step in show_contents:

                if 'color' in step:
                    step['color'] = self._get_color(step['color'])
                    if len(str(step['color'])) > 2:
                        YamlInterface.rename_key('color', '(leds)', step,
                                                 self.log)
                        step['leds'] = CommentedMap()
                        YamlInterface.copy_with_comments(step, '(leds)',
                                                         step['leds'], '(leds)', True, self.log)
                    else:
                        YamlInterface.rename_key('color', '(lights)', step,
                                                 self.log)
                        step['lights'] = CommentedMap()
                        YamlInterface.copy_with_comments(step, '(lights)',
                                                         step['lights'], '(lights)', True, self.log)

    def _get_old_default_text_styles(self):
        # these are from MPF 0.21, but they are in the new v4 format
        text_styles = '''
          default:
            font_name: Quadrit
            font_size: 10
            crop_top: 2
            crop_bottom: 3
          space title huge:
            font_name: DEADJIM
            font_size: 29
            antialias: true
            crop_top: 3
            crop_bottom: 3
          space title:
            font_name: DEADJIM
            font_size: 21
            antialias: true
            crop_top: 2
            crop_bottom: 3
          medium:
            font_name: pixelmix
            font_size: 8
            crop_top: 1
            crop_bottom: 1
          small:
            font_name: smallest_pixel-7
            font_size: 9
            crop_top: 2
            crop_bottom: 3
          tall title:
            font_name: big_noodle_titling
            font_size: 20
        '''

        return yaml.load(text_styles, Loader=MpfRoundTripLoader)

    def is_show_file(self):
        # Verify we have a show file and that it's an old version
        if 'tocks' in self.fc[0]:
            return True

    def _migrate_show_file(self):
        self.log.debug("Migrating show file: %s", self.file_name)

        show_name_stub = os.path.splitext(os.path.split(self.file_name)[1])[0]

        self._add_show_version()

        # Convert tocks to time
        self._convert_tocks_to_time(self.fc)

        # migrate the components in each step
        self.log.debug("Converting settings for each show step")

        slide_num = 0

        for i, step in enumerate(self.fc):

            self._remove_tags(step)

            if 'display' in step:
                self.log.debug("Show step %s: Converting 'display' section",
                               i+1)

                found_transition = False
                for widget in step['display']:
                    if 'transition' in widget:
                        found_transition = True
                        break

                if found_transition:
                    step['display'] = dict(
                        widgets=self._migrate_elements(step['display']))

                    for widget in step['display']['widgets']:

                        self._convert_tokens(widget)

                        if 'transition' in widget:
                            YamlInterface.copy_with_comments(
                                widget, 'transition', step['display'],
                                'transition', True, self.log)

                else:
                    step['display'] = self._migrate_elements(step['display'])
                    self._convert_tokens(step['display'])

                YamlInterface.rename_key('display', 'slides', step)

                slide_num += 1
                old_slides = step['slides']
                step['slides'] = dict()
                step['slides']['{}_slide_{}'.format(show_name_stub,
                                                    slide_num)] = old_slides

        return True

    def _add_show_version(self):
        # Do a str.replace to preserve any spaces or comments in the header

        try:
            self.fc.ca.comment[1][0].value = ('#show_version=4\n{}'.format(
                self.fc.ca.comment[1][0].value))
        except TypeError:
            self.fc.yaml_set_start_comment('show_version=4\n')

        self.log.debug('Adding #show_version=4')

    def _convert_tocks_to_time(self, show_steps):
        self.log.debug('Converting "tocks:" to "time:" and cascading entries '
                       'to the next step (since time: is for the current '
                       'step versus tocks: being for the previous step)')
        previous_tocks = 0
        for i, step in enumerate(show_steps):
            previous_tocks = step['tocks']

            if not i:
                step['tocks'] = 0
            else:
                step['tocks'] = '+{}'.format(previous_tocks)

            YamlInterface.rename_key('tocks', 'time', step, self.log)

        if len(show_steps) > 1:
            show_steps.append(CommentedMap())
            show_steps[-1]['time'] = '+{}'.format(previous_tocks)

        return show_steps

    def _remove_tags(self, dic):
        found = False
        for k, v in dic.items():
            if isinstance(v, dict):
                for k1 in v.keys():
                    if k1.startswith('tag|'):
                        YamlInterface.rename_key(k1, k1.strip('tag|'), v)
                        found = True
                        break

        if found:
            self._remove_tags(dic)

    def _convert_tokens(self, dic):
        # converts % tokens to ()
        token_finder = re.compile("(?<=%)[a-zA-Z_0-9|]+(?=%)")

        if isinstance(dic, list):
            for step in dic:
                if 'text' in step:
                    for token in token_finder.findall(step['text']):
                        step['text'] = step['text'].replace(
                            '%{}%'.format(token), '({})'.format(token))

        else:
            if 'text' in dic:
                for token in token_finder.findall(dic['text']):
                    dic['text'] = dic['text'].replace(
                        '%{}%'.format(token), '({})'.format(token))


def migrate_file(file_name, file_content):
    return V4Migrator(file_name, file_content).migrate()
