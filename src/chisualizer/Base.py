from collections import OrderedDict
import copy
import logging
from numbers import Number
import yaml
import os

from chisualizer.util import Rectangle

# A registry of all visualization descriptors (map from tag name to class)
# which can be instantiated.
tag_registry = {}
def tag_register(tag_name=None):
  def wrap(cls):
    assert tag_name not in tag_registry, "Duplicate tag '%s'" % tag_name
    tag_registry[tag_name] = cls
    return cls
  return wrap

# A registry of desugaring structural transforms, a map from the tag name to a
# function which takes in the ParsedElement and desugars it via in-place 
# mutation. The desugared element must have a different tag.
# Desugaring runs in the same order as elements were read in, postorder.
# Returned elements may alias with other elements. 
# TODO: is aliasing a good idea? should ParsedElements be able to clone()?
# TODO: is the ordering guarantee good enough? better ways to do that?  
desugar_tag_registry = {} 
def desugar_tag(tag_name):
  def wrap(fun):
    assert tag_name not in desugar_tag_registry, "Duplicate desugaring tag '%s'" % tag_name
    desugar_tag_registry[tag_name] = fun
    return fun
  return wrap

# A registry of desugaring structural transforms, run on ALL tagged elements.
# Same guarantees and conditions as the tagged version, except the desugared
# version need not have a different tag. Each registered desugaring function
# runs once per ParsedElement, and there are no guarantees on the ordering of
# the degsugaring functions.
desugar_all_registry = []
def desugar_all():
  def wrap(fun):
    assert fun not in desugar_all_registry, "Duplicate desugaring function %s" % fun
    desugar_all_registry.append(fun)
    return fun
  return wrap

@desugar_tag("Ref")
def desugar_ref(parsed_element, registry):
  if len(parsed_element.get_attr_list('ref')) != 1:
    parsed_element.parse_error("Can only have one ref")
  ref_name = parsed_element.get_attr_list('ref')[0]
  assert isinstance(ref_name, basestring) # TODO: more elegant typing in desugaring
  ref = registry.get_ref(ref_name)
  if ref is None:
    parsed_element.parse_error("Ref not found: '%s'" % ref_name)
  assert ref.tag != "Ref" # should have been already desugared
  if 'path' in parsed_element.attr_map:
    path_prefix = parsed_element.get_attr_list('path')[0]
  else:
    path_prefix = None
  
  parsed_element.tag = ref.tag
  parsed_element.attr_map = copy.copy(ref.attr_map)
  
  # TODO: make this mechanism more general?
  if path_prefix is not None:
    if 'path' in parsed_element.attr_map:
      # TODO: should type check things here
      parsed_element.attr_map['path'] = [path_prefix + path_elt
                                         for path_elt 
                                         in parsed_element.attr_map['path']]
    else:
      parsed_element.attr_map['path'] = path_prefix

def apply_template(element, template):
  for template_attr, template_value_list in template.attr_map.iteritems():
    if template_attr not in element.attr_map:
      element.attr_map[template_attr] = []
    element.attr_map[template_attr].extend(template_value_list)
      
@desugar_all()
def desugar_template(parsed_element, registry):
  if 'template' in parsed_element.attr_map:
    # TODO handle multiple templates
    assert len(parsed_element.get_attr_list('template')) == 1, "TODO Generalize me"
    template_name = parsed_element.get_attr_list('template')[0]
    del parsed_element.attr_map['template']
    template = registry.get_ref(template_name)
    if template is None:
      parsed_element.parse_error("Template not found: '%s'" % template_name)
    if template.tag != 'Template':
      # TODO: is this checking too strict?
      parsed_element.parse_error("Not a template: '%s'" % template_name)
    assert "template" not in template.attr_map  # should have already been desugared
    apply_template(parsed_element, template)

class YAMLVisualizerRegistry():
  def __init__(self):
    self.default_templates = OrderedDict()
    self.lib_elements = {}
    self.display_elements = []

  def get_ref(self, ref_name):
    """Returns the referenced ParsedElement or None"""
    return self.lib_elements.get(ref_name, None)

  def process_default_template(self, name, elt):
    """Processes a default template, like inheriting parents (by the 
    corresponding class hierarchy) and doing various sanity checks."""
    if elt.tag != "Template":
      elt.parse_error("Default templates must be Template tag")
    if name not in tag_registry:
      elt.parse_error("Unrecognized tag '%s'" % name)
    cls = tag_registry[name]
    
    # Ensure no children have already been parsed as defaults.
    for loaded_name in self.default_templates.iterkeys():
      loaded_cls = tag_registry[loaded_name]
      if issubclass(loaded_cls, cls):
        elt.parse_error("Child tag defaults for '%s' loaded before parent '%s'"
                        % (loaded_name, name))
    
    # Find closest parent and apply as template
    for parent_name, parent_elt in reversed(self.default_templates.items()):
      if issubclass(cls, tag_registry[parent_name]):
        apply_template(elt, parent_elt)
        break
      
  def apply_default_template(self, elt):
    """Applies the default template to an element. Should only be run after
    the element has been fully desugared."""
    if elt.tag in self.default_templates:
      apply_template(elt, self.default_templates[elt.tag])

  def read_descriptor(self, filename):
    """Reads in a YAML descriptor."""
    loader = yaml.SafeLoader(open(filename).read())
    
    desugar_queue = []
    
    # https://stackoverflow.com/questions/13319067/parsing-yaml-return-with-line-number
    def compose_node(parent, index):
      line = loader.line
      node = yaml.composer.Composer.compose_node(loader, parent, index)
      node.__line__ = line + 1
      return node
        
    def create_obj_constructor(tag_name):
      def obj_constructor(loader, node):
        elt = ParsedElement(tag_name, loader.construct_mapping(node),
                            filename, node.__line__)
        desugar_queue.append(elt)
        return elt
        
        # TODO: add line numbers
      return obj_constructor
    
    loader.compose_node = compose_node
    
    for tag_name in tag_registry:
      loader.add_constructor("!" + tag_name, create_obj_constructor(tag_name))
      
    for tag_name in desugar_tag_registry:
      loader.add_constructor("!" + tag_name, create_obj_constructor(tag_name))
    
    yaml_dict = loader.get_data()
    if 'defaults' in yaml_dict:
      assert isinstance(yaml_dict['defaults'], dict)
      for tag_name, elt in yaml_dict['defaults'].iteritems():
        self.process_default_template(tag_name, elt)
        logging.debug("Loaded default template tag='%s'", tag_name)
        self.default_templates[tag_name] = elt
    logging.debug("Finished loading default templates")
    
    if 'lib' in yaml_dict:
      assert isinstance(yaml_dict['lib'], dict)
      for ref_name, elt in yaml_dict['lib'].iteritems():
        # Check is a parsedelement
        elt.set_ref(ref_name)
        if ref_name in self.lib_elements:
          elt.parse_error("Duplicate ref")
        logging.debug("Loaded library element ref='%s'", ref_name)
        self.lib_elements[ref_name] = elt
    logging.debug("Finished loading library elements")
    
    if 'display' in yaml_dict:
      assert isinstance(yaml_dict['display'], list)
      for idx, elt in enumerate(yaml_dict['display']):
        elt.set_ref("(display %i)" % idx)
        self.display_elements.append(elt)
    logging.debug("Finished loading display elements")
      
    # Run desugaring pass
    for elt in desugar_queue:
      while elt.tag in desugar_tag_registry:
        old_tag = elt.tag
        desugar_tag_registry[elt.tag](elt, self)
        assert elt.tag != old_tag
        
      for desugar_all_fn in desugar_all_registry:
        desugar_all_fn(elt, self)
      
      self.apply_default_template(elt)
      
    logging.debug("Finished desugaring pass")
      
class VisualizerRoot(object):
  """An visualizer descriptor file."""
  def __init__(self, filename, api):
    """Initialize this descriptor from a file and given a ChiselApi object."""
    from chisualizer.visualizers.Theme import DarkTheme
    
    # Hacks to get this to behave as a AbstractVisualizer
    # TODO: FIX, perhaps with guard node
    self.root = self
    self.path = ""
    
    self.api = api
    self.theme = DarkTheme()
    self.registry = YAMLVisualizerRegistry()
    self.visualizer = None  # TODO: support multiple visualizers in different windows

    self.registry.read_descriptor(os.path.dirname(__file__) + "/vislib.yaml")
    self.registry.read_descriptor(filename)
    
    if not self.registry.display_elements:
      raise VisualizerParseError("No display elements specified.")
    
    if len(self.registry.display_elements) != 1:
      raise VisualizerParseError("Multiple visualizers currently not supported.")
    
    from chisualizer.visualizers.VisualizerBase import AbstractVisualizer
    self.visualizer = self.registry.display_elements[0].instantiate(self, valid_subclass=AbstractVisualizer)

  def update(self):
    self.visualizer.update()

  def layout_cairo(self, cr):
    size_x, size_y = self.visualizer.layout_cairo(cr)
    return Rectangle((0, 0), (size_x, size_y))
  
  def draw_cairo(self, cr, rect):
    return self.visualizer.draw_cairo(cr, rect, 0)

  def get_theme(self):
    # TODO refactor this, probably makes more sense to set themes here
    return self.theme

  def set_theme(self, theme):
    # TODO: is persistent theme state really the best idea?
    self.theme = theme

  def get_api(self):
    return self.api

class Base(object):
  """Abstract base class for visualizer descriptor objects."""
  def __init__(self, element, parent):
    self.parent = parent
    self.root = parent.root
    self.ref = element.get_ref()
  
  def get_chisel_api(self):
    """Returns the ChiselApi object used to access node values.
    Returns None if not available or if this visualizer wasn't properly
    instantiated."""
    return self.root.get_chisel_api()
  
  def get_theme(self):
    """Returns a Theme object, mapping descriptions to numerical colors."""
    return self.root.get_theme()

class VisualizerParseError(BaseException):
  """Base exception class for errors during visualizer descriptor parsing."""
  pass

class VisualizerParseTypeError(VisualizerParseError):
  """Type conversion (string->?) failed during parsing."""
  pass

class VisualizerParseValidationError(VisualizerParseError):
  """Attribute validation failed during parsing."""
  pass

class VisualizerParseTagError(VisualizerParseError):
  """Something was wrong with the element tag."""
  pass

class VisualizerParseAttributeError(VisualizerParseError):
  """Something was wrong with the attributes."""
  pass

class VisualizerParseAttributeNotFound(VisualizerParseError):
  """A required attribute was not found."""
  pass

class VisualizerParseAttributeNotUsed(VisualizerParseError):
  """A specified attribute was not used."""
  pass

class ElementAttr(object):
  """Object representing an attribute, handling dynamic values and data type
  conversions."""
  def parse_error(self, message, **kwds):
    self.element.parse_error("Error in attribute %s: %s"
                             % (self.attr_name, message),
                             **kwds)
  
  def __init__(self, parent, element, attr_name):
    self.parent = parent
    self.element = element
    self.attr_name = attr_name
    self.attr_values = self.create_value_list(element.get_attr_list(attr_name))

  def update(self):
    """Call to update dynamic values."""
    raise NotImplementedError()

  def create_value_list(self, attr_value_list):
    """Parses the attr's value list and returns it in a format suitable for
    internal use. Does type checking here; can raise parsing errors."""
    raise NotImplementedError()

  def get_static(self):
    """Returns the static value for this attribute. Raises an error if the
    attribute contains a non-static value."""
    raise NotImplementedError()
  
  def get_dynamic(self):
    """Returns the dynamic value for this attribute. update() must have been
    called before."""
    raise NotImplementedError()

class ObjectAttr(ElementAttr):
  """Straight pass-through of the attribute value list, for uncommon attribute
  types where a general framework is not worth the trouble."""
  def __init__(self, parent, element, attr_name):
    super(ObjectAttr, self).__init__(parent, element, attr_name)

  def update(self):
    pass

  def create_value_list(self, attr_value_list):
    return attr_value_list

  def get_static(self):
    return self.attr_values
  
  def get_dynamic(self):
    return self.attr_values

class SingleElementAttr(ElementAttr):
  """ElementAttribute subclass for attributes using the first valid element of 
  the value list."""
  def create_value_list(self, attr_value_list):
    parsed_value_list = []
    for attr_value_elt in attr_value_list:
      parsed_value_list.append(self.create_value_elt(attr_value_elt))
    return parsed_value_list
    
  def create_value_elt(self, attr_value_elt):
    """create_value_list becomes a wrapper for this, which handles the case for
    a single value element."""
    raise NotImplementedError()
  
  def value_elt_to_data(self, value_elt, static=False):
    """Converts a value list element to data, or returns None if the element
    cannot produce the relevant data (and to move onto the next element in the
    list. Raises an error if static if False but the element is dynamic."""
    raise NotImplementedError()

  def update(self):
    for value_elt in self.attr_values:
      conv = self.value_elt_to_data(value_elt, static=False)
      if conv is not None:
        self.dynamic_value = conv
        return
    self.parse_error("No valid value in list",
                     exc_cls=VisualizerParseValidationError)

  def get_static(self):
    for value_elt in self.attr_values:
      conv = self.value_elt_to_data(value_elt, static=True)
      if conv is not None:
        return conv
    self.parse_error("No valid value in list",
                     exc_cls=VisualizerParseValidationError)
  
  def get_dynamic(self):
    return self.dynamic_value
  
class StringAttr(SingleElementAttr):
  def __init__(self, parent, element, attr_name, valid_set=None):
    super(StringAttr, self).__init__(parent, element, attr_name)
    self.valid_set = valid_set
  
  def create_value_elt(self, attr_value_elt):
    if isinstance(attr_value_elt, basestring):
      return attr_value_elt
    elif isinstance(attr_value_elt, ParsedElement):
      from chisualizer.display.VisualizerToString import VisualizerToString
      return attr_value_elt.instantiate(self.parent, 
                                        valid_subclass=VisualizerToString)
    else:
      self.parse_error("Invalid type for '%s': %s"
                       % (attr_value_elt, attr_value_elt.__class__.__name__),
                       exc_cls=VisualizerParseValidationError)
  
  def value_elt_to_data(self, value_elt, static=False):
    from chisualizer.display.VisualizerToString import VisualizerToString # TODO HACKY
    if isinstance(value_elt, basestring):
      conv = value_elt
    elif isinstance(value_elt, VisualizerToString):
      conv = value_elt.get_string(self.parent)
    else:
      assert False, "Unknown type: %s" % value_elt.__class__.__name__
    
    if self.valid_set is not None and conv not in self.valid_set:
      self.parse_error("%s='%s' not in valid set: %s" 
                       % (self.attr_name, conv, self.valid_set),
                       exc_cls=VisualizerParseValidationError)
      
    return conv

  def get_longest_strings(self):
    from chisualizer.display.VisualizerToString import VisualizerToString # TODO HACKY
    longest_strings = []
    for value_elt in self.attr_values:
      if isinstance(value_elt, basestring):
        longest_strings.append(value_elt)
      elif isinstance(value_elt, VisualizerToString):
        longest_strings.extend(value_elt.get_longest_strings(self.parent))
      else:
        assert False, "Unknown type: %s" % value_elt.__class__.__name__
    return longest_strings
  
  def can_set_from_string(self):
    """Returns whether set_from_string can possibly succeed or will always 
    fail."""
    from chisualizer.display.VisualizerToString import VisualizerToString # TODO HACKY
    for value_elt in self.attr_values:
      if isinstance(value_elt, VisualizerToString):
        if value_elt.can_set_from_string(self.parent):
          return True
    return False
  
  def set_from_string(self, set_string):
    """Attempt to set the node of the text being displayed using an arbitrary
    input string. Returns True if successful, False otherwise."""
    from chisualizer.display.VisualizerToString import VisualizerToString # TODO HACKY
    for value_elt in self.attr_values:
      if isinstance(value_elt, VisualizerToString):
        if value_elt.set_from_string(self.parent, set_string):
          return True
    return False
  
class IntAttr(SingleElementAttr):
  def __init__(self, parent, element, attr_name, 
               valid_min=None, valid_max=None):
    super(IntAttr, self).__init__(parent, element, attr_name)
    self.valid_min = valid_min
    self.valid_max = valid_max
  
  def create_value_elt(self, attr_value_elt):
    if isinstance(attr_value_elt, basestring):
      try:
        return int(attr_value_elt, 0)
      except ValueError:
        self.parse_error("Can't covert '%s' to int" % attr_value_elt,
                         exc_cls=VisualizerParseTypeError)
    elif isinstance(attr_value_elt, Number):
      return int(attr_value_elt)
    elif isinstance(attr_value_elt, ParsedElement):
      from chisualizer.display.VisualizerToString import VisualizerToString
      return attr_value_elt.instantiate(self.parent, 
                                        valid_subclass=VisualizerToString)
    else:
      self.parse_error("Invalid type in '%s': %s"
                       % (attr_value_elt, attr_value_elt.__class__.__name__),
                       exc_cls=VisualizerParseValidationError)
  
  def value_elt_to_data(self, value_elt, static=False):
    if isinstance(value_elt, int):
      conv = value_elt
    else:
      assert False, "Unknown type: %s" % value_elt.__class__.__name__ 
    
    if self.valid_min is not None and conv < self.valid_min:
      self.parse_error("%i < min (%i)" % (conv, self.valid_min),
                       exc_cls=VisualizerParseValidationError)
    if self.valid_max is not None and conv > self.valid_max:
      self.parse_error("%i > max (%i)" % (conv, self.valid_max),
                       exc_cls=VisualizerParseValidationError)
            
    return conv  
  
class ElementAccessor(object):
  """Accessor object for ParsedElement. Provides a method to track accesses,
  to ensure all attributes specified in the descriptors are actually used.
  """
  def parse_error(self, *args, **kwargs):
    self.parent.parse_error(*args, **kwargs)
  
  def __init__(self, parent_element):
    self.parent = parent_element
    self.accessed_attrs = set()
  
  def get_ref(self):
    return self.parent.ref
  
  def attrs_not_accessed(self):
    return self.parent.get_all_attrs() - self.accessed_attrs
  
  def get_attr_list(self, attr):
    self.accessed_attrs.add(attr)
    return self.parent.get_attr_list(attr)
  
class ParsedElement(object):
  """
  An intermediate representation for parsed visualizer descriptor objects -
  essentially a dict of the element attributes and list of children.
  """
  def parse_error(self, message, exc_cls=VisualizerParseError):
    """Helper function to throw a fatal error, indicating the broken element
    along with filename and line number.
    """
    raise exc_cls("Error parsing %s '%s' (%s:%i): %s" % 
                  (self.tag, self.ref, self.filename, self.lineno, message))
  
  def __init__(self, tag_name, attr_map, filename, lineno):
    self.tag = tag_name
    self.attr_map = self.canonicalize_attr_map(attr_map)
    self.filename = filename
    self.lineno = lineno
    self.ref = '(anon)'
  
  def set_ref(self, ref):
    self.ref = ref
  
  @staticmethod
  def canonicalize_attr_map(attr_map):
    """Canonicalizes the attr map, making everything that isn't a list into a
    list. Modification is done in-place."""
    for attr, val in attr_map.iteritems():
      if not isinstance(val, list):
        attr_map[attr] = [val] 
    return attr_map
  
  def instantiate(self, parent, valid_subclass=None):
    assert valid_subclass is not None
    if self.tag not in tag_registry:
      self.parse_error("Unknown tag '%s'" % self.tag,
                       exc_cls=VisualizerParseTagError)
      
    rtn_cls = tag_registry[self.tag]
    if not issubclass(rtn_cls, valid_subclass):
      self.parse_error("Expected to be a subclass of %s" %
                       valid_subclass.__name__,
                       exc_cls=VisualizerParseTagError)
        
    logging.debug("Instantiating %s (%s:%s)" % 
                  (rtn_cls.__name__, self.tag, self.ref))
    
    accessor = ElementAccessor(self)
    rtn = rtn_cls(accessor, parent)
    if accessor.attrs_not_accessed():
      self.parse_error("Unused attributes: %s" % accessor.attrs_not_accessed())
    
    return rtn
    
    
  def get_all_attrs(self):
    return set(self.attr_map.iterkeys())
    
  def get_attr_list(self, attr):
    if attr not in self.attr_map:
      self.parse_error("Cannot find attribute: '%s'" % attr,
                       exc_cls=VisualizerParseAttributeNotFound)
    return self.attr_map[attr]
  