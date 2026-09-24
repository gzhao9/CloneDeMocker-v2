package excluded;

class ExcludedTest {
    void testIgnored() {
        Object ignored = org.mockito.Mockito.mock(Object.class);
    }
}
