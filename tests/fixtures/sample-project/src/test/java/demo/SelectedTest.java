package demo;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class SelectedTest {
    interface Dependency { String load(); }

    void testFirst() {
        Dependency dependency = mock(Dependency.class);
        when(dependency.load()).thenReturn("first");
    }

    void testSecond() {
        Dependency dependency = mock(Dependency.class);
        when(dependency.load()).thenReturn("second");
    }
}
