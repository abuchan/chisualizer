from chisualizer.visualizers.VisualizerBase import AbstractVisualizer
import chisualizer.Base as Base

class Modifier(Base.Base):
  """Modifies the dynamic attributes of target visualizers."""
  def __init__(self, elt, parent):
    super(Modifier, self).__init__(elt, parent)
    modify_attrs_list = self.static_attr(Base.ObjectAttr, 'modify_attrs').get()
    assert len(modify_attrs_list) == 1, "Chained modify_attrs not supported yet"
    self.modify_attrs = modify_attrs_list[0]

  def apply_to(self, target):
    assert isinstance(target, AbstractVisualizer)
    target.apply_attr_overloads(self, self.modify_attrs)

@Base.tag_register('ArrayIndexModifier')
class ArrayIndexModifier(Modifier):
  """A modifier that works on arrays, modifying the child visualizer at some
  index."""
  def __init__(self, elt, parent):
    super(ArrayIndexModifier, self).__init__(elt, parent)
    self.path_component = self.static_attr(Base.StringAttr, 'index_path').get()    
    self.node = parent.get_node_ref().get_child_reference(self.path_component)
    if not self.node.has_value():
      elt.parse_error("index_path node '%s' has no value" % self.node)
    
  def get_array_index(self):
    """Returns the array index to modify"""
    return self.node.get_value()
  
@Base.tag_register('CondArrayIndexModifier')
class CondArrayIndexModifier(ArrayIndexModifier):
  """Conditional modifier, only applies when some node is not zero."""
  def __init__(self, elt, parent):
    super(CondArrayIndexModifier, self).__init__(elt, parent)
    self.cond_path_component = self.static_attr(Base.StringAttr, 'cond_path').get()    
    self.cond_node = parent.get_node_ref().get_child_reference(self.cond_path_component)
    if not self.cond_node.has_value():
      elt.parse_error("cond_path node '%s' has no value" % self.node)
    
  def get_array_index(self):
    """Returns the array index to modify"""
    return self.node.get_value()
  
  def apply_to(self, target):
    if self.cond_node.get_value() != 0:
      super(CondArrayIndexModifier, self).apply_to(target)
    
    
