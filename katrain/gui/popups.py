from collections import defaultdict
from typing import Dict, List, DefaultDict, Tuple
import re

from kivy.clock import Clock
from kivy.properties import StringProperty, BooleanProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.selectioncontrol import MDCheckbox
from kivymd.uix.textfield import MDTextField

from katrain.core.utils import OUTPUT_DEBUG, OUTPUT_ERROR
from katrain.core.engine import KataGoEngine
from katrain.core.game import Game, GameNode
from katrain.gui.kivyutils import StyledSpinner
from katrain.gui.style import DEFAULT_FONT


class I18NPopup(Popup):
    title_key = StringProperty("")
    font_name = StringProperty(DEFAULT_FONT)


class LabelledTextInput(MDTextField):
    input_property = StringProperty("")
    multiline = BooleanProperty(False)

    @property
    def input_value(self):
        return self.text


class LabelledCheckBox(MDCheckbox):
    input_property = StringProperty("")

    def __init__(self, text=None, **kwargs):
        if text is not None:
            kwargs["active"] = text.lower() == "true"
        super().__init__(**kwargs)

    @property
    def input_value(self):
        return bool(self.active)


class LabelledSpinner(StyledSpinner):
    input_property = StringProperty("")

    @property
    def input_value(self):
        return self.selected[1]  # ref value


class LabelledFloatInput(LabelledTextInput):
    signed = BooleanProperty(True)
    pat = re.compile("[^0-9-]")

    def insert_text(self, substring, from_undo=False):
        pat = self.pat
        if "." in self.text:
            s = re.sub(pat, "", substring)
        else:
            s = ".".join([re.sub(pat, "", s) for s in substring.split(".", 1)])
        r = super().insert_text(s, from_undo=from_undo)
        if not self.signed and "-" in self.text:
            self.text = self.text.replace("-", "")
        elif self.text and "-" in self.text[1:]:
            self.text = self.text[0] + self.text[1:].replace("-", "")
        return r

    @property
    def input_value(self):
        return float(self.text)


class LabelledIntInput(LabelledTextInput):
    pat = re.compile("[^0-9]")

    def insert_text(self, substring, from_undo=False):
        return super().insert_text(re.sub(self.pat, "", substring), from_undo=from_undo)

    @property
    def input_value(self):
        return int(self.text)


class InputParseError(Exception):
    pass


class QuickConfigGui(MDBoxLayout):
    def __init__(self, katrain):
        super().__init__()
        self.katrain = katrain
        self.popup = None
        Clock.schedule_once(lambda _dt: self.set_properties(self))

    def collect_properties(self, widget):
        if isinstance(widget, (LabelledTextInput, LabelledSpinner, LabelledCheckBox)) and getattr(widget, "input_property", None):
            try:
                ret = {widget.input_property: widget.input_value}
            except Exception as e:
                raise InputParseError(f"Could not parse value for {widget.input_property} ({widget.__class__}): {e}")  # TODO : on widget!
        else:
            ret = {}
        for c in widget.children:
            for k, v in self.collect_properties(c).items():
                ret[k] = v
        return ret

    def get_setting(self, key):
        keys = key.split("/")
        config = self.katrain._config
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        if keys[-1] not in config:
            config[keys[-1]] = ""
            self.katrain.log(f"Configuration setting {repr(key)} was missing, created it, but this likely indicates a broken config file.", OUTPUT_ERROR)
        return config[keys[-1]], config, keys[-1]

    def set_properties(self, widget):
        if isinstance(widget, (LabelledTextInput, LabelledSpinner, LabelledCheckBox)) and getattr(widget, "input_property", None):
            value = self.get_setting(widget.input_property)[0]
            if isinstance(widget, LabelledCheckBox):
                widget.active = value is True
            elif isinstance(widget, LabelledSpinner):
                selected = 0
                try:
                    selected = widget.value_refs.index(value)
                except:
                    pass
                widget.text = widget.values[selected]
            else:
                widget.text = str(value)
        for c in widget.children:
            self.set_properties(c)

    def update_config(self, save_to_file=True):
        updated = set()
        for multikey, value in self.collect_properties(self).items():
            old_value, conf, key = self.get_setting(multikey)
            if value != old_value:
                self.katrain.log(f"Updating setting {multikey} = {value}", OUTPUT_DEBUG)
                conf[key] = value  # reference straight back to katrain._config
                updated.add(multikey)
        if save_to_file:
            self.katrain.save_config()
        if updated:
            self.katrain.update_state()
        if self.popup:
            self.popup.dismiss()
        return updated


class ConfigTimerPopup(QuickConfigGui):
    def __init__(self, katrain):
        super().__init__(katrain)

    def update_config(self, save_to_file=True):
        super().update_config(save_to_file=save_to_file)
        for p in self.katrain.game.players.values():
            p.periods_used = 0
        self.katrain.controls.timer.paused = True
        self.katrain.game.current_node.time_used = 0
        self.katrain.update_state()


class NewGamePopup(QuickConfigGui):
    def __init__(self, katrain):
        super().__init__(katrain)
        self.rules_spinner.value_refs = [name for abbr, name in katrain.engine.RULESETS_ABBR]

    def update_config(self, save_to_file=True):
        updated = super().update_config(save_to_file=save_to_file)
        self.katrain.log(f"New game settings: {self.katrain.config('game')}", OUTPUT_DEBUG)
        if self.restart.active:
            self.katrain.log("Restarting Engine", OUTPUT_DEBUG)
            self.katrain.engine.restart()
        self.katrain("new-game")


class ConfigPopup(QuickConfigGui):
    def __init__(self, katrain, popup: Popup, config: Dict, ignore_cats: Tuple = (), **kwargs):
        self.config = config
        self.ignore_cats = ignore_cats
        self.orientation = "vertical"
        super().__init__(katrain, popup, **kwargs)
        Clock.schedule_once(self.build, 0)

    def build(self, _):

        props_in_col = [0, 0]
        cols = [BoxLayout(orientation="vertical"), BoxLayout(orientation="vertical")]

        for k1, all_d in sorted(self.config.items(), key=lambda tup: -len(tup[1])):  # sort to make greedy bin packing work better
            if k1 in self.ignore_cats:
                continue
            d = {k: v for k, v in all_d.items() if isinstance(v, (int, float, str, bool)) and not k.startswith("_")}  # no lists . dict could be supported but hard to scale
            cat = GridLayout(cols=2, rows=len(d) + 1, size_hint=(1, len(d) + 1))
            cat.add_widget(Label(text=""))
            cat.add_widget(ScaledLightLabel(text=f"{k1} settings", bold=True))
            for k2, v in d.items():
                label = ScaledLightLabel(text=f"{k2}:")
                widget = self.type_to_widget_class(v)(text=str(v), input_property=f"{k1}/{k2}")
                hint = all_d.get("_hint_" + k2)
                if hint:
                    label.tooltip_text = hint
                    if isinstance(widget, LabelledTextInput):
                        widget.hint_text = hint
                cat.add_widget(label)
                cat.add_widget(widget)
            if props_in_col[0] <= props_in_col[1]:
                cols[0].add_widget(cat)
                props_in_col[0] += len(d)
            else:
                cols[1].add_widget(cat)
                props_in_col[1] += len(d)

        col_container = BoxLayout(size_hint=(1, 0.9))
        col_container.add_widget(cols[0])
        col_container.add_widget(cols[1])
        self.add_widget(col_container)
        self.info_label = Label(halign="center")
        self.apply_button = StyledButton(text="Apply", on_press=lambda _: self.update_config())
        self.save_button = StyledButton(text="Apply and Save", on_press=lambda _: self.update_config(save_to_file=True))
        btn_container = BoxLayout(orientation="horizontal", size_hint=(1, 0.1), spacing=1, padding=1)
        btn_container.add_widget(self.apply_button)
        btn_container.add_widget(self.info_label)
        btn_container.add_widget(self.save_button)
        self.add_widget(btn_container)

    def update_config(self, save_to_file=False):
        updated_cat = defaultdict(list)  # type: DefaultDict[List[str]]
        try:
            for k, v in self.collect_properties(self).items():
                k1, k2 = k.split("/")
                if self.config[k1][k2] != v:
                    self.katrain.log(f"Updating setting {k} = {v}", OUTPUT_DEBUG)
                    updated_cat[k1].append(k2)
                    self.config[k1][k2] = v
            self.popup.dismiss()
        except InputParseError as e:
            self.info_label.text = str(e)
            self.katrain.log(e, OUTPUT_ERROR)
            return

        if save_to_file:
            self.katrain.save_config()

        engine_updates = updated_cat["engine"]
        if "visits" in engine_updates:
            self.katrain.engine.visits = engine_updates["visits"]
        if {key for key in engine_updates if key not in {"max_visits", "max_time", "enable_ownership", "wide_root_noise"}}:
            self.katrain.log(f"Restarting Engine after {engine_updates} settings change")
            self.info_label.text = "Restarting engine\nplease wait."
            self.katrain.controls.set_status(f"Restarted Engine after {engine_updates} settings change.")

            def restart_engine(_dt):
                old_engine = self.katrain.engine  # type: KataGoEngine
                old_proc = old_engine.katago_process
                if old_proc:
                    old_engine.shutdown(finish=True)
                new_engine = KataGoEngine(self.katrain, self.config["engine"])
                self.katrain.engine = new_engine
                self.katrain.game.engines = {"B": new_engine, "W": new_engine}
                self.katrain.game.analyze_all_nodes()  # old engine was possibly broken, so make sure we redo any failures
                self.katrain.update_state()

            Clock.schedule_once(restart_engine, 0)

        self.katrain.debug_level = self.config["debug"]["level"]
        self.katrain.update_state(redraw_board=True)


class LoadSGFPopup(BoxLayout):
    pass


class ConfigTeacherPopup(QuickConfigGui):
    def __init__(self, katrain, popup, **kwargs):
        self.settings = katrain.config("trainer")
        self.sgf_settings = katrain.config("sgf")
        self.ui_settings = katrain.config("board_ui")
        super().__init__(katrain, popup, self.settings, **kwargs)
        self.spacing = 2
        Clock.schedule_once(self.build, 0)

    def build(self, _dt):
        thresholds = self.settings["eval_thresholds"]
        undos = self.settings["num_undo_prompts"]
        colors = self.ui_settings["eval_colors"]
        thrbox = GridLayout(spacing=1, padding=2, cols=5, rows=len(thresholds) + 1)
        thrbox.add_widget(ScaledLightLabel(text="Point loss greater than", bold=True))
        thrbox.add_widget(ScaledLightLabel(text="Gives this many undos", bold=True))
        thrbox.add_widget(ScaledLightLabel(text="Color (fixed)", bold=True))
        thrbox.add_widget(ScaledLightLabel(text="Show dots", bold=True))
        thrbox.add_widget(ScaledLightLabel(text="Save in SGF", bold=True))
        for i, (thr, undos, color) in enumerate(zip(thresholds, undos, colors)):
            thrbox.add_widget(LabelledFloatInput(text=str(thr), input_property=f"eval_thresholds::{i}"))
            thrbox.add_widget(LabelledFloatInput(text=str(undos), input_property=f"num_undo_prompts::{i}"))
            thrbox.add_widget(BackgroundMixin(background_color=color[:3]))
            thrbox.add_widget(LabelledCheckBox(text=str(color[3] == 1), input_property=f"alpha::{i}"))
            thrbox.add_widget(LabelledCheckBox(size_hint=(0.5, 1), text=str(self.sgf_settings["save_feedback"][i]), input_property=f"save_feedback::{i}"))
        self.add_widget(thrbox)

        xsettings = BoxLayout(size_hint=(1, 0.15), spacing=2)
        xsettings.add_widget(ScaledLightLabel(text="Show last <n> dots"))
        xsettings.add_widget(LabelledIntInput(size_hint=(0.5, 1), text=str(self.settings["eval_off_show_last"]), input_property="eval_off_show_last"))
        self.add_widget(xsettings)
        xsettings = BoxLayout(size_hint=(1, 0.15), spacing=2)
        xsettings.add_widget(ScaledLightLabel(text="Show dots/SGF comments for AI players"))
        xsettings.add_widget(LabelledCheckBox(size_hint=(0.5, 1), text=str(self.settings["eval_show_ai"]), input_property="eval_show_ai"))
        self.add_widget(xsettings)
        xsettings = BoxLayout(size_hint=(1, 0.15), spacing=2)
        xsettings.add_widget(ScaledLightLabel(text="Disable analysis while in teach mode"))
        xsettings.add_widget(LabelledCheckBox(size_hint=(0.5, 1), text=str(self.settings["lock_ai"]), input_property="lock_ai"))
        self.add_widget(xsettings)

        bl = BoxLayout(size_hint=(1, 0.15), spacing=2)
        bl.add_widget(StyledButton(text=f"Apply", on_press=lambda _: self.update_config(False)))
        self.info_label = Label()
        bl.add_widget(self.info_label)
        bl.add_widget(StyledButton(text=f"Apply and Save", on_press=lambda _: self.update_config(True)))
        self.add_widget(bl)

    def update_config(self, save_to_file=False):
        try:
            for k, v in self.collect_properties(self).items():
                if "::" in k:
                    k1, i = k.split("::")
                    i = int(i)
                    if "alpha" in k1:
                        v = 1.0 if v else 0.0
                        if self.ui_settings["eval_colors"][i][3] != v:
                            self.katrain.log(f"Updating alpha {i} = {v}", OUTPUT_DEBUG)
                            self.ui_settings["eval_colors"][i][3] = v
                    elif "save_feedback" in k1:
                        if self.sgf_settings[k1][i] != v:
                            self.sgf_settings[k1][i] = v
                            self.katrain.log(f"Updating setting sgf/{k1}[{i}] = {v}", OUTPUT_DEBUG)

                    else:
                        if self.settings[k1][i] != v:
                            self.settings[k1][i] = v
                            self.katrain.log(f"Updating setting trainer/{k1}[{i}] = {v}", OUTPUT_DEBUG)
                else:
                    if self.settings[k] != v:
                        self.settings[k] = v
                        self.katrain.log(f"Updating setting {k} = {v}", OUTPUT_DEBUG)
            if save_to_file:
                self.katrain.save_config()
            self.popup.dismiss()
        except InputParseError as e:
            self.info_label.text = str(e)
            self.katrain.log(e, OUTPUT_ERROR)
            return
        self.katrain.update_state()
        self.popup.dismiss()
